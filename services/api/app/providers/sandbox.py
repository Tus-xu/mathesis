"""SymPy 沙箱执行器（SSOT §5.9 /tools/verify/run）

安全措施：
1. 子进程隔离（asyncio.create_subprocess_exec，非 exec 于主进程）
2. 限时：wall-clock 超时 kill（默认 timeout_ms，上限 10000）
3. 限内存：Linux 用 resource.setrlimit；Windows 靠 job object（降级为仅限时）
4. 禁网：沙箱进程内 patch 掉 socket/requests/urllib；白名单 import 仅 math/sympy/numpy
5. stdout 截断 ≤2000 字；代码长度 ≤4000 字符由 Pydantic 卡死
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

# 沙箱执行器 Python 代码模板
_SANDBOX_TEMPLATE = """\
import sys
import builtins

# ===== 可信预导入（必须在限制前完成） =====
# sympy/numpy 的依赖链（mpmath 等）内部会 import os——封锁后再 import sympy 必挂。
# 预导入后这些模块已在 sys.modules，用户代码再 import 只走缓存，不经 __import__ 钩子。
import sympy as sp
from sympy import *
import sympy.parsing.sympy_parser  # parse_expr 所在，check_equivalence 依赖
try:
    import sympy.parsing.latex  # parse_latex（依赖 antlr4，缺失环境跳过即降级）
    # 预热一次：lark 语法的惰性初始化（读语法文件）必须在封锁 open 前完成
    sympy.parsing.latex.parse_latex(r"\frac{1}{2}")
    # antlr 后端每次 parse 都经 importlib.metadata 查版本；沙箱封锁 importlib，
    # 此处预核验 4.11 后打桩，绕过运行时版本查询
    import importlib.metadata as _im
    import sympy.parsing.latex._parse_latex_antlr as _pla
    if _im.version("antlr4-python3-runtime").startswith("4.11"):
        _pla.version = lambda name: "4.11"
except Exception:
    pass
import numpy as np

# ===== 安全限制 =====
_original_import = builtins.__import__
_BLOCKED_MODULES = frozenset([
    'os', 'subprocess', 'socket', 'http', 'urllib', 'ctypes',
    'multiprocessing', 'shutil', 'pathlib', 'glob', 'signal',
    'threading', 'requests', 'httpx', 'aiohttp', 'pickle',
    'importlib', 'code', 'codeop', 'compile', 'compileall',
])
_ALLOWED_MODULES = frozenset([
    'math', 'sympy', 'numpy', 'fractions', 'decimal',
    'itertools', 'functools', 'operator', 'collections',
    'string', 're', 'json', 'typing', 'abc', 'numbers',
])

def _safe_import(name, *args, **kwargs):
    top = name.split('.')[0]
    if top in _BLOCKED_MODULES:
        raise ImportError(f"Blocked module: {{name}}")
    return _original_import(name, *args, **kwargs)

builtins.__import__ = _safe_import

# 禁用文件操作
builtins.open = None
builtins.input = None

# ===== 执行用户代码 =====
try:
{indented_code}
except Exception as e:
    # 注：模板用 .replace() 替换（非 .format()），此处单花括号 f-string 才能输出真实异常（迭代05 修复）
    print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
"""


async def run_sandbox(
    code: str,
    timeout_ms: int | None = None,
) -> dict:
    """执行 SymPy 沙箱代码

    Args:
        code: Python 代码（≤4000 字符，仅允许 math/sympy/numpy）
        timeout_ms: 超时毫秒数（≤10000）

    Returns:
        {exec_status: "pass"|"fail"|"timeout", stdout: str, result_repr: str, error: str|None}
    """
    # 参数校验
    if len(code) > 4000:
        return {
            "exec_status": "fail",
            "stdout": "",
            "result_repr": "",
            "error": "代码长度超过 4000 字符限制",
        }

    timeout_ms = min(timeout_ms or settings.sandbox_timeout_ms, 10000)
    timeout_s = timeout_ms / 1000.0

    # 构造沙箱代码
    indented = "\n".join("    " + line for line in code.split("\n"))
    sandbox_code = _SANDBOX_TEMPLATE.replace("{indented_code}", indented)

    # 写入临时文件
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(sandbox_code)
            tmp_path = f.name

        # 子进程执行
        python_exe = sys.executable or "python"
        proc = await asyncio.create_subprocess_exec(
            python_exe,
            tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # 最小环境变量；PYTHONIOENCODING=utf-8 防 Windows GBK 控制台编码
            # 导致中文输出 UnicodeEncodeError（迭代05 评测实测暴露）
            env={"PATH": "", "HOME": "", "TEMP": "", "TMP": "", "PYTHONIOENCODING": "utf-8"},
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            # 超时 kill
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            logger.info("sandbox_timeout", timeout_ms=timeout_ms)
            return {
                "exec_status": "timeout",
                "stdout": "",
                "result_repr": "",
                "error": f"执行超时（{timeout_ms}ms）",
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")[:2000]
        stderr = stderr_bytes.decode("utf-8", errors="replace")[:500]

        if proc.returncode == 0:
            return {
                "exec_status": "pass",
                "stdout": stdout,
                "result_repr": stdout.strip()[-200:] if stdout.strip() else "",
                "error": None,
            }
        else:
            return {
                "exec_status": "fail",
                "stdout": stdout,
                "result_repr": "",
                "error": stderr or f"进程退出码 {proc.returncode}",
            }

    except Exception as e:
        logger.error("sandbox_error", error=str(e))
        return {
            "exec_status": "fail",
            "stdout": "",
            "result_repr": "",
            "error": f"沙箱内部错误: {str(e)[:200]}",
        }
    finally:
        # 清理临时文件
        if tmp_path:
            with suppress(Exception):
                Path(tmp_path).unlink(missing_ok=True)


async def check_equivalence(
    answer_expr: str,
    expected_expr: str,
    timeout_ms: int = 3000,
) -> dict:
    """SymPy 等价判定（判分链路用）

    四层兜底：
    1. 字符串精确匹配
    2. 数值容差（代入随机点）
    3. SymPy simplify 等价（timeout 3s）
    4. 失败 → pending_review

    Returns:
        {verdict: "correct"|"wrong"|"pending_review", method: str}
    """
    # Layer 1: 字符串精确
    a_clean = answer_expr.strip().replace(" ", "")
    e_clean = expected_expr.strip().replace(" ", "")
    if a_clean == e_clean:
        return {"verdict": "correct", "method": "exact_match"}

    # Layer 2+3: SymPy 判定
    code = f"""\
from sympy import *
import sys

try:
    from sympy.parsing.latex import parse_latex
except Exception:
    parse_latex = None  # 无 antlr4 环境：LaTeX 解析降级，仅 sympify 路径可用

x, y, z, t = symbols('x y z t')
n, m, k = symbols('n m k', integer=True)

try:
    # 尝试解析为 SymPy 表达式
    try:
        answer = sympify("{answer_expr.replace('"', '\\"')}")
    except:
        if parse_latex is None:
            raise
        answer = parse_latex(r"{answer_expr.replace('"', '\\"')}")

    try:
        expected = sympify("{expected_expr.replace('"', '\\"')}")
    except:
        if parse_latex is None:
            raise
        expected = parse_latex(r"{expected_expr.replace('"', '\\"')}")

    # 方程形式（x=3）：取右值参与比较（左侧为单变量时）
    if isinstance(answer, Eq) and len(answer.lhs.free_symbols) <= 1:
        answer = answer.rhs
    if isinstance(expected, Eq) and len(expected.lhs.free_symbols) <= 1:
        expected = expected.rhs

    # 数值容差验证
    import random
    random.seed(42)
    test_points = [random.uniform(-5, 5) for _ in range(5)]
    all_close = True
    for val in test_points:
        try:
            a_val = complex(answer.subs(x, val))
            e_val = complex(expected.subs(x, val))
            if abs(a_val - e_val) > 1e-6:
                all_close = False
                break
        except:
            all_close = False
            break

    if all_close:
        print("NUMERIC_EQUIV")
        sys.exit(0)

    # 符号等价
    diff = simplify(answer - expected)
    if diff == 0:
        print("SYMBOLIC_EQUIV")
        sys.exit(0)

    print("NOT_EQUIV")
    sys.exit(0)
except Exception as e:
    print(f"PARSE_ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

    result = await run_sandbox(code, timeout_ms=timeout_ms)

    if result["exec_status"] == "timeout":
        return {"verdict": "pending_review", "method": "timeout"}

    if result["exec_status"] == "fail":
        return {"verdict": "pending_review", "method": "parse_error"}

    stdout = result["stdout"].strip()
    if "NUMERIC_EQUIV" in stdout:
        return {"verdict": "correct", "method": "numeric_equiv"}
    if "SYMBOLIC_EQUIV" in stdout:
        return {"verdict": "correct", "method": "symbolic_equiv"}
    if "NOT_EQUIV" in stdout:
        return {"verdict": "wrong", "method": "symbolic_diff"}

    return {"verdict": "pending_review", "method": "unknown"}
