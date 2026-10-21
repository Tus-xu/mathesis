"""GeoGebra 交互图形生成（AI → GGB 构造命令）

背景：figure_renderer 的参数化 SVG 对复杂几何/立体图形"又错又丑"。
本模块改为让 AI 直接生成**可执行的 GeoGebra 命令脚本**，前端 DynamicFigureViewer
灌入 GeoGebra 画布 → 2D/3D 交互（拖拽 / 缩放 / 旋转 / 滑块动点）。

复用参考内核（D:\\ref\\，供联调对照）：
- Any2GGB：命令速查 / 避坑清单（prompt 规则与 2D/3D 构造要点）
- awesome-geogebra-ai：ggbValidation 命令白名单校验（本文件 Python 移植）
- MathMover（产品）：上传截图 → AI → GeoGebra 画布（同思路，本模块覆盖文本/图片两路）

命令安全：仅允许纯构造 / 样式命令 + 赋值形式；禁止脚本注入
（javascript: / Execute / RunClickScript / OpenFile / Button / InputBox 等）。

公开 API：
    extract_ggb_lines(text) -> list[str]                # LLM 输出 -> 命令行
    validate_ggb_commands(commands) -> tuple[list[str], list[str]]
    detect_view(question_text, commands) -> str         # 2d / 3d
    build_ggb_payload(commands, view, caption) -> dict  # {"type":"ggb", ...}
    async generate_ggb(question_text, *, figure_hint=None, interactive=False,
                       user_id=None, db=None) -> dict | None
"""

from __future__ import annotations

import base64
import re
import uuid

import structlog

logger = structlog.get_logger(__name__)

MAX_COMMAND_LENGTH = 420
MAX_COMMANDS = 160

# 允许的创建类命令（awesome-geogebra-ai ggbValidation 白名单 + 3D 立体）
ALLOWED_COMMANDS = {
    "Angle", "AngleBisector", "Area", "Center", "Circle", "Circumcircle",
    "Cone", "Cube", "Cylinder", "Distance", "Ellipse", "Function",
    "Hyperbola", "Intersect", "IntersectPath", "Line", "Locus", "Midpoint",
    "OrthogonalLine", "ParallelLine", "ParallelPlane", "Plane", "Parabola",
    "PerpendicularBisector", "PerpendicularLine", "Point", "Polygon",
    "Polyhedron", "Polyline", "Prism", "Pyramid", "Ray", "Root", "Segment",
    "Semicircle", "Slider", "Sphere", "Tangent", "Tetrahedron", "Text",
    "Vector", "Vertex",
}

# 允许的脚本/样式类命令
ALLOWED_STYLE = {
    "SetCaption", "SetColor", "SetFilling", "SetFixed", "SetLabelMode",
    "SetLabelStyle", "SetLayer", "SetLineStyle", "SetLineThickness",
    "SetPointSize", "SetPointStyle", "SetVisible", "ShowLabel",
    "SetAnimating", "StartAnimation",
}

# 禁止片段（防注入）
FORBIDDEN_FRAGMENTS = (
    "javascript:", "<script", "</script", "ggbApplet", "eval(", "Function(",
    "fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "document.",
    "window.", "RunClickScript", "RunUpdateScript", "SetValue", "Execute",
    "Delete", "OpenFile", "URL", "Button", "InputBox",
)

LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
COORD_RE = re.compile(r"^[0-9A-Za-z_+\-*/^().\sπ°]+$")
ALLOWED_FUNCS = {
    "abs", "acos", "asin", "atan", "ceil", "cos", "exp", "floor", "ln",
    "log", "round", "sin", "sqrt", "tan",
}

# 指令行（非 GGB 命令，但控制视窗/透视/步骤）
DIRECTIVE_RE = re.compile(
    r"^\s*#\s*(perspective\s*:\s*(2d|3d)|view\s*:.*|view3d\s*:.*|step[_ ]?\d+.*)$",
    re.IGNORECASE,
)
COMMENT_RE = re.compile(r"^\s*#")
ASSIGN_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=")
CALL_RE = re.compile(
    r"^\s*(?:[A-Za-z][A-Za-z0-9_]*\s*=\s*)?([A-Za-z][A-Za-z0-9_]*)\s*(?:\(|\[)"
)


def _is_safe_coord(expr: str) -> bool:
    if not expr or not COORD_RE.match(expr):
        return False
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9_]*)\s*\(", expr):
        fn = m.group(1).lower()
        if fn not in ALLOWED_FUNCS and not re.match(r"^[a-z][A-Za-z0-9_]*$", m.group(1)):
            return False
    return True


def _split_args(source: str) -> list[str] | None:
    args: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(source):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
        elif ch == "," and depth == 0:
            args.append(source[start:i].strip())
            start = i + 1
    if depth != 0:
        return None
    args.append(source[start:].strip())
    return args


def _is_point_assignment(cmd: str) -> bool:
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*\((.*)\)\s*$", cmd)
    if not m or not LABEL_RE.match(m[1]):
        return False
    coords = _split_args(m[2])
    return bool(
        coords and len(coords) in (2, 3) and all(_is_safe_coord(c) for c in coords)
    )


def _is_numeric_assignment(cmd: str) -> bool:
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", cmd)
    return bool(m and LABEL_RE.match(m[1]) and _is_safe_coord(m[2]))


def _is_function_assignment(cmd: str) -> bool:
    m = re.match(r"^\s*[a-z][A-Za-z0-9_]*\s*\(\s*x\s*\)\s*=\s*(.+?)\s*$", cmd)
    return bool(m and _is_safe_coord(m[1]))


def _is_labeled_equation(cmd: str) -> bool:
    m = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.+?)\s*=\s*(.+?)\s*$", cmd)
    if not m or not LABEL_RE.match(m[1]):
        return False
    left, right = m.group(2), m.group(3)
    has_xy = re.search(r"[xy]", left) or re.search(r"[xy]", right)
    return bool(has_xy and _is_safe_coord(left) and _is_safe_coord(right))


def _normalize_brackets(cmd: str) -> str:
    """把 Slider[a,0,5] 之类的方括号旧语法归一为圆括号（兼容 Awesome 规范）。"""
    m = re.match(r"^\s*(?:([A-Za-z][A-Za-z0-9_]*)\s*=\s*)?([A-Za-z][A-Za-z0-9_]*)\s*\[([\s\S]*)\]\s*$", cmd)
    if not m:
        return cmd
    label, name, body = m.group(1), m.group(2), m.group(3)
    if name not in ALLOWED_COMMANDS and name not in ALLOWED_STYLE:
        return cmd
    return f"{label}={name}({body})" if label else f"{name}({body})"


def validate_ggb_command(cmd: str) -> tuple[bool, str]:
    """单条 GGB 命令校验：返回 (ok, 原因)。"""
    c = cmd.strip()
    if not c:
        return False, "空命令"
    if len(c) > MAX_COMMAND_LENGTH:
        return False, "命令过长"
    if "\n" in c or "\r" in c:
        return False, "每行只能一条命令"
    if DIRECTIVE_RE.match(c) or COMMENT_RE.match(c):
        return True, ""  # 指令/注释行放行（前端解析执行）
    if FORBIDDEN_FRAGMENTS and any(f.lower() in c.lower() for f in FORBIDDEN_FRAGMENTS):
        return False, "命令包含被禁止的操作"
    if re.search(r"[{}<>]|;", c):
        return False, "命令包含不支持的标点"
    c = _normalize_brackets(c)
    if (
        _is_point_assignment(c)
        or _is_numeric_assignment(c)
        or _is_function_assignment(c)
        or _is_labeled_equation(c)
    ):
        return True, ""
    m = CALL_RE.match(c)
    if not m:
        return False, "必须是受支持的 GeoGebra 构造命令"
    name = m.group(1)
    if name in ALLOWED_COMMANDS or name in ALLOWED_STYLE:
        return True, ""
    return False, f"{name} 不在命令白名单中"


def validate_ggb_commands(commands: list[str]) -> tuple[list[str], list[str]]:
    """批量校验：返回 (有效命令, 被拒原因)。去重 + 上限。"""
    valid: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for raw in commands[:MAX_COMMANDS]:
        ok, reason = validate_ggb_command(raw)
        if ok:
            key = raw.strip()
            if key not in seen:
                valid.append(raw.strip())
                seen.add(key)
        else:
            rejected.append(f"{raw.strip()[:80]} → {reason}")
    return valid, rejected


def extract_ggb_lines(text: str) -> list[str]:
    """LLM 输出 -> 命令行：剥 markdown 围栏/杂文，逐行取命令与指令行。"""
    if not text:
        return []
    cleaned = re.sub(r"```(?:ggb|geogebra|txt)?\s*(.*?)```", r"\1", text, flags=re.DOTALL | re.IGNORECASE)
    lines: list[str] = []
    for raw in cleaned.splitlines():
        s = raw.strip()
        if not s:
            continue
        if COMMENT_RE.match(s) and not DIRECTIVE_RE.match(s):
            continue  # 普通注释跳过；# view / # perspective / # step 保留
        lines.append(s)
    return lines


def detect_view(question_text: str, commands: list[str]) -> str:
    """按题目/命令判定 2d/3d：显式 # perspective 优先，其次题目关键词。"""
    for c in commands:
        m = re.match(r"^\s*#\s*perspective\s*:\s*(2d|3d)\s*$", c, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    text = question_text or ""
    if re.search(
        r"立体|空间|棱锥|棱柱|棱台|正方体|长方体|球|外接球|内切球|截面|"
        r"三棱|四棱|圆锥|圆柱|三视图|体积|表面积|面|法向量|平面",
        text,
    ):
        return "3d"
    # 命令里出现三维点/立体对象 → 3d
    if any(re.search(r"=\s*\([^)]*,[^)]*,[^)]*\)", c) for c in commands):
        return "3d"
    if any(
        re.search(r"\b(Cube|Prism|Pyramid|Sphere|Cone|Cylinder|Tetrahedron|Polyhedron|Plane)\b", c)
        for c in commands
    ):
        return "3d"
    return "2d"


def build_ggb_payload(commands: list[str], view: str, caption: str = "") -> dict:
    """序列化 GGB 载荷（写入 image 列 / 传输给前端）。"""
    return {
        "type": "ggb",
        "view": view if view in ("2d", "3d") else "2d",
        "commands": commands,
        "caption": str(caption or "")[:120],
    }


# ---------------------------------------------------------------------------
# 题型 profile 检测 + 分 profile 专业构造规则（移植 awesome-geogebra-ai promptProfiles）
# ---------------------------------------------------------------------------

_PROFILE_SOLID_RE = re.compile(
    r"立体|空间|棱锥|棱柱|棱台|正方体|长方体|四面体|三棱|四棱|球|外接球|内切球|"
    r"圆锥|圆柱|三视图|截面|截割|线面角|二面角|异面直线|体积|表面积|法向量|平面"
)
_PROFILE_ANALYTIC_RE = re.compile(
    r"解析几何|椭圆|抛物线|双曲线|圆锥曲线|焦点|准线|离心率|弦|轨迹|切线|割线|"
    r"动点|垂足|x\^2|y\^2|x²|y²|ellipse|parabola|hyperbola"
)
_PROFILE_FUNCTION_RE = re.compile(
    r"函数|二次函数|导数|单调|极值|最值|最大值|最小值|零点|根|区间|f\(x\)|y="
)


def detect_profile(question_text: str, view: str) -> str:
    """判定题型 profile：solid_geometry / analytic_geometry / function / geometry。"""
    text = question_text or ""
    if view == "3d" or _PROFILE_SOLID_RE.search(text):
        return "solid_geometry"
    if _PROFILE_ANALYTIC_RE.search(text):
        return "analytic_geometry"
    if _PROFILE_FUNCTION_RE.search(text):
        return "function"
    return "geometry"


GGB_BASE_PROMPT = """你是 GeoGebra 命令生成器。输入一道数学题（可含题图视觉描述），输出可直接在 GeoGebra 输入栏逐行执行的命令脚本。

铁律（违反即废稿）：
1. 一行一条命令，不写分号、不写多语句；注释用 `# 中文`，命令全用英文。
2. 对象标签英文字母+数字（A、B1、f、tri），中文只出现在 SetCaption(对象,"中文") 引号内。
3. 定义即创建：A=(1,2)、f(x)=x^2、c=Circle(A,3)；同名重复定义会覆盖旧对象，每个标签只定义一次。
4. 数学表达式乘号写显式 *：2*cos(t)、a*x^2（禁隐式乘法）。
5. 字符串一律英文双引号，禁中文引号/逗号/括号。
6. 每个可见线对象都要 SetColor 为深色（黑 0,0,0 或深靛 40,60,120 / 深红 170,40,40 / 深绿 30,110,60），白底可印；禁止浅色/白色线条。Polygon 的可见边另建 Polyline(A,B,C,A) 或逐边 Segment 设深色。
7. 视窗：第 1~2 行写 `# perspective: 2d` 或 `# perspective: 3d`；2D 写 `# view: xmin ymin xmax ymax`（按题目数据算好、留 10%~20% 边距）；3D 可写 `# view3d: xmin ymin zmin xmax ymax zmax`。严禁 ZoomIn/ZoomOut。
8. LaTeX 文本：Text("a^2+b^2=c^2",(x,y),true,true)（第4参 true=LaTeX），严禁给 LaTeX 文本 SetColor。
9. 命令白名单（只能用这些）：Point Segment Line Ray Vector Polygon Polyline Circle Ellipse Parabola Hyperbola Function Intersect Midpoint PerpendicularLine ParallelLine PerpendicularBisector Angle AngleBisector Tangent Locus Slider Text Sphere Cube Prism Pyramid Cylinder Cone Tetrahedron Polyhedron Plane ParallelPlane 等。样式命令：SetColor SetLineThickness SetLineStyle SetPointSize SetFilling ShowLabel SetCaption SetAnimating StartAnimation。
10. 交点多解必须用 P=Intersect(c1,c2,1) 与 Q=Intersect(c1,c2,2) 分别取。
11. 只输出命令脚本文本：无 markdown 围栏、无解释文字。按绘图步骤用 `# step_01：<说明>` 分段，每段 2~8 行，全片 2~5 步。
12. 先定义后引用：每个对象在使用前必须先定义；严禁给匿名表达式设样式（如 SetLineThickness(Segment(P,H),2)，必须先 h=Segment(P,H) 再 SetLineThickness(h,2)）。
"""

_PROFILE_PROMPTS = {
    "solid_geometry": """【题型：立体几何 3D】必须严格遵循：
- 用真 3D 坐标定义全部空间点：A=(x,y,z)。
- 先定义全部顶点，再逐条画可见棱：对棱柱/棱锥/立方体/多面体，先用命名 Segment 画出每条可见结构棱；隐藏/辅助棱用灰色虚线（SetLineStyle 虚线 + 浅灰 90,90,100），关键被研究对象用加粗深色。
- 可见面/辅助面用有限 Polygon，填充透明度 ≤0.04（SetFilling(poly,0.04)），不得遮挡立体；纯计算用的无限 Plane 画完 SetVisible(plane,false)。
- 截面方向必须保留：底面 z=0 时，水平截面用 z=h，垂直截面用 x=s 或 y=s；未指定方向时必须用带方向与位置参数的任意平面 + Intersect(plane,polyhedron)，不得默认成水平截面。
- 二面角 C-AB-D：AB 是两半平面公共棱，不能用普通点角 ∠CAD 替代；要同时显示 AB、两半平面（或代表面）、到 AB 的垂距、垂直截面。
- 线面角用 Angle(Line(D,E),planeName)。
- 动点（到某线距离固定）用原生 Slider 参数化轴向位置与径向角：如 t=Slider(0,1,0.01)，点由参数定义，保证每个滑块值都满足固定距离。
- 不要用 Text 复制点名：关键点用 ShowLabel(A,true)，棱/面 ShowLabel(edge,false)。
- 棱柱按题目真实尺寸坐标化（如长方体 AB=2、BC=1、AA1=√3 → A=(0,0,0) B=(2,0,0) C=(2,1,0) D=(0,1,0) A1=(0,0,sqrt(3))…），不要随手画立方体。
""",
    "analytic_geometry": """【题型：解析几何 / 圆锥曲线】必须严格遵循：
- 圆锥曲线用焦点/顶点定义：Ellipse(F1,F2,2a)、Hyperbola(F1,F2,2a)、Parabola(F,l)。
- 题目提到的焦点、准线、弦、中点、垂足、切线、割线、轨迹、距离、面积，每个都要显式构造并标注。
- 两交点必须 P=Intersect(curve,l,1)、Q=Intersect(curve,l,2) 分别取。
- 面积题：目标三角形/四边形必须是命名 Polygon + 半透明蓝色填充，且补全边界线段。
- 垂足命名 H=...，辅助高/投影段命名 height=Segment(P,H)。
- 动点/线族用一个主 Slider 控制（t=Slider(min,max,step)），轨迹辅助命名 locus=Locus(...)。
- 坐标须数学上真实（如椭圆 x²/a²+y²/b²=1 的焦点 (±c,0)，c=sqrt(a²-b²)），不得随意占位。
""",
    "function": """【题型：函数图像】必须严格遵循：
- 先画函数图像，再标根、极值、区间端点、切线/割线交点。
- 区间问题：显式构造端点（在图像上的点 + 向 x 轴投影 + 区间底线段）。
- 极值/单调：可视标出顶点/关键点，不要只写公式。
- 切线/割线演示：命名切线/割线并区分样式。
- 参数变化（斜率/截距/开口/区间端点）暴露为 Slider 并关联。
""",
    "geometry": """【题型：平面几何】必须严格遵循：
- 原题点用可拖动的自由点（除非题目给坐标）；几何关系显式构造：PerpendicularLine、ParallelLine、AngleBisector、Circle、Segment、Polygon、Midpoint、Intersect。
- 题目提到的三角形/四边形/圆/角/角平分线/高/中线/切线/垂线必须可见地出现在命令里。
- 证明题只加揭示思路所需的辅助线（灰色虚线），原边保持深色。
- 面积题构造并填充题目命名的精确多边形区域。
""",
}


def build_ggb_system_prompt(profile: str) -> str:
    return GGB_BASE_PROMPT + "\n" + _PROFILE_PROMPTS.get(profile, _PROFILE_PROMPTS["geometry"])


# 兼容旧引用：缺省 geometry profile
GGB_SYSTEM_PROMPT = build_ggb_system_prompt("geometry")


def file_bytes_to_data_uri(data: bytes, mime: str = "image/jpeg", max_side: int = 720, quality: int = 82) -> str:
    """图片字节 -> 压缩后 data URI（视觉模型 token 友好；PIL 缺失则原样 base64）。"""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


async def resolve_image_data_uri(db, file_id, user_id) -> str | None:
    """按 file_id 读取属主图片文件并压缩为 data URI（供视觉读图）；失败返回 None。"""
    try:
        import tempfile
        from pathlib import Path

        from sqlalchemy import select

        from app.models.file import File

        result = await db.execute(
            select(File).where(
                File.id == file_id,
                File.user_id == user_id,
                File.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None or row.file_type != "image":
            return None
        if (row.storage_uri or "").startswith("local:"):
            path = (
                Path(tempfile.gettempdir())
                / "math-arena-file-uploads"
                / str(row.user_id)
                / f"{row.id}.bin"
            )
            data = path.read_bytes()
        else:
            from app.providers.storage import get_storage

            data = get_storage().get_bytes(row.storage_uri)
        return file_bytes_to_data_uri(data, row.mime or "image/jpeg")
    except Exception as e:
        logger.warning("geogebra.file_read_failed", file_id=str(file_id), error=str(e)[:120])
        return None


async def _call_vision_byok(
    image_data_uri: str,
    question_text: str,
    figure_hint: str | None,
    profile: str,
    interactive: bool,
) -> str | None:
    """BYOK 视觉通道：配置了支持图片的 OpenAI 兼容多模态模型时，直接看图生成 GGB（MathMover 内核）。

    返回模型输出的命令文本；未配置/调用失败返回 None（调用方降级 wf_doc_understand 读图）。
    """
    from app.config import settings

    if not (settings.vision_base_url and settings.vision_api_key and settings.vision_model):
        return None
    system = build_ggb_system_prompt(profile)
    hint_block = f"\n图形说明：{figure_hint.strip()[:300]}" if figure_hint and figure_hint.strip() else ""
    interaction = (
        "\n要求：本题需要体现动态过程，请包含滑杆/动点并关联关键对象（至多 1 个 StartAnimation）。"
        if interactive
        else "\n要求：默认静态配图，忠实反映题目，不主动加滑杆/动画。"
    )
    text_block = (
        f"题目：{question_text.strip()[:1200]}{hint_block}{interaction}\n\n"
        "请仔细阅读这张数学题图片：先完整识别题干与配图中的几何结构（图形类型、顶点/交点/关键点位置、"
        "边/线/曲线、平行垂直等关系、长度角度标注、平面或立体），再按图中真实结构逐行生成 GeoGebra 命令。"
        "必须与图一致，不得臆造图形。"
    )
    payload = {
        "model": settings.vision_model,
        "temperature": 0.2,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text_block},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            },
        ],
    }
    headers = {"Authorization": f"Bearer {settings.vision_api_key}", "Content-Type": "application/json"}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(settings.vision_base_url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning("geogebra.vision_http_failed", status=resp.status_code, body=resp.text[:120])
                return None
            data = resp.json()
        choices = data.get("choices") or []
        return (choices[0].get("message") or {}).get("content") or None
    except Exception as e:
        logger.warning("geogebra.vision_failed", error=str(e)[:150])
        return None


async def _read_figure_vision(image_data_uri: str, user_id: str | None, db) -> str:
    """降级视觉读图：用星火 wf_doc_understand 提取题图文字/图形信息（best-effort）。

    返回并入 prompt 的视觉描述块；失败返回 ""（不影响主链路）。
    """
    try:
        from app.providers.xingchen import resolve_effective_xingchen_config, run_workflow

        cfg = None
        if db is not None and user_id:
            try:
                cfg = await resolve_effective_xingchen_config(db, str(user_id))
            except Exception:
                cfg = None
        result = await run_workflow(
            "wf_doc_understand",
            uid=str(user_id) if user_id else "anonymous",
            parameters={
                "AGENT_USER_INPUT": (
                    "识别这张数学题图片：1) 提取完整题干与全部选项文字（含 LaTeX）；"
                    "2) 重点描述配图中的几何结构：图形类型、所有顶点/交点/关键点及其位置关系、"
                    "边/线/曲线、平行垂直等关系、长度角度数值标注、是平面还是立体图形。"
                ),
                "image_url": image_data_uri,
                "task": "extract_question",
                "grade_hint": "G3",
            },
            config=cfg,
        )
        qt = str(result.get("question_text") or "").strip()
        if qt and "无法识别" not in qt:
            return f"\n题图视觉识别（请据此忠实构造图形）：{qt[:600]}"
    except Exception as e:
        logger.warning("geogebra.vision_read_failed", error=str(e)[:150])
    return ""


async def _run_llm_generate(
    system_prompt: str,
    user_prompt: str,
    *,
    user_id: str | None,
    db,
) -> dict | None:
    """文本模型生成 GGB：带白名单校验 + 一次失败反馈重试。返回 {"commands", "view"}。"""
    from app.providers.router import get_model_router, get_model_router_for_user

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    attempt = 0
    last_commands: list[str] = []
    while attempt < 2:
        attempt += 1
        rid = f"ggb-{uuid.uuid4().hex[:12]}"
        try:
            router = (
                await get_model_router_for_user(user_id, db)
                if db is not None and user_id
                else get_model_router()
            )
            result = await router.chat(
                messages,
                temperature=0.2,
                max_tokens=4000,
                request_id=rid,
                scene="geogebra_figure",
            )
        except Exception as e:  # 模型不可用：降级静态图，不阻断主链路
            logger.warning("geogebra.llm_failed", error=str(e)[:150])
            return None

        text = str((result or {}).get("content") or "").strip()
        lines = extract_ggb_lines(text)
        valid, rejected = validate_ggb_commands(lines)
        if not valid:
            if attempt == 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "以下命令未通过校验，请全部重写为合法 GeoGebra 命令：\n"
                            + "\n".join(rejected[:12])
                            + "\n\n注意：只用白名单命令；每行一条；# 开头为指令/注释。"
                        ),
                    }
                )
            continue
        last_commands = valid
        real = [c for c in valid if not COMMENT_RE.match(c)]
        if not real:
            return None
        break

    if not last_commands:
        return None
    view = detect_view("", last_commands)
    return {"commands": last_commands, "view": view}


async def generate_ggb(
    question_text: str,
    *,
    figure_hint: str | None = None,
    interactive: bool = False,
    image_data_uri: str | None = None,
    user_id: str | None = None,
    db=None,
) -> dict | None:
    """AI 生成 GeoGebra 交互构造（MathMover 同款内核）。

    视觉优先级：
    1. 配置了 BYOK 视觉模型（settings.vision_*）→ 直接看图生成（与题图精确一致）；
    2. 未配置视觉模型但有原图 → 星火 wf_doc_understand 读图提取图形信息，并入文本模型 prompt；
    3. 纯文本（引导解题/函数题）→ 文本模型 + 分 profile 专业构造规则。

    返回 {"commands": [...], "view": "2d"|"3d"}；失败返回 None（调用方降级静态图）。
    """
    if not (question_text or "").strip() and not (figure_hint or "").strip():
        return None

    view_hint = detect_view(question_text, [])
    profile = detect_profile(question_text, view_hint)
    system = build_ggb_system_prompt(profile)

    # 1) BYOK 视觉通道（看图直接生成，最接近原图）
    if image_data_uri:
        vision_text = await _call_vision_byok(image_data_uri, question_text, figure_hint, profile, interactive)
        if vision_text:
            lines = extract_ggb_lines(vision_text)
            valid, _rejected = validate_ggb_commands(lines)
            real = [c for c in valid if not COMMENT_RE.match(c)]
            if valid and real:
                return {"commands": valid, "view": detect_view(question_text, valid)}

        # 2) 降级：wf_doc_understand 读图提取图形信息
        vision_context = await _read_figure_vision(image_data_uri, user_id, db)
    else:
        vision_context = ""

    hint_block = f"\n图形说明：{figure_hint.strip()[:300]}" if figure_hint and figure_hint.strip() else ""
    interaction = (
        "\n要求：本题需要体现动态过程，请包含滑杆/动点并关联关键对象（至多 1 个 StartAnimation）。"
        if interactive
        else "\n要求：默认静态配图，忠实反映题目，不主动加滑杆/动画。"
    )
    user_prompt = (
        f"题目：{question_text.strip()[:1200]}"
        f"{hint_block}{vision_context}{interaction}"
        f"\n\n输出：GeoGebra 命令脚本（先 `# perspective: {view_hint}` 定透视，再按步骤构造）。"
    )
    return await _run_llm_generate(system, user_prompt, user_id=user_id, db=db)
