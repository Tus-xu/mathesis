"""RV11 · 真实 Linux 沙箱攻击/资源/复现套件（**真实 Docker 容器**，无 Docker 自动跳过）。

两类探测都跑在真实 Linux 容器里（`math-arena-sandbox` 镜像，non-root + read-only rootfs
+ `--network=none` + cgroups）：

- **guard-on**（与 research-vnext 生产路径一致：module allowlist 恒开）；
- **guard-off**（直接调用 sandbox 包并显式关闭 AST 守卫）—— 用于证明**即使守卫被绕过，
  内核层隔离仍然兜底**（defense in depth 的真实证据，不是口号）。

验收红线：任何用例都不允许"跳过=通过"；Docker 不可用时显式 skip 并写明原因。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SANDBOX_SRC = Path(__file__).resolve().parents[3] / "sandbox" / "src"
if str(SANDBOX_SRC) not in sys.path:
    sys.path.insert(0, str(SANDBOX_SRC))

try:
    from sandbox.models import ExecutionRequest, ResourceLimits, SandboxPolicy
    from sandbox.orchestrator import run_experiment

    HAS_SANDBOX = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_SANDBOX = False


def _docker_available() -> bool:
    if not HAS_SANDBOX:
        return False
    try:
        from sandbox.backends.container import ContainerBackend

        ok, _ = ContainerBackend().is_available()
        return ok
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="真实 Docker 不可用：容器级证据需要真实 Linux 运行时"
)

IMAGE = os.getenv("RESEARCH_SANDBOX_IMAGE", "math-arena-sandbox:0.2.0")


def _run(code: str, *, guard: bool = True, seed: int = 42, **limit_over):
    limits = dict(cpu_limit=1.0, memory_limit_mb=256, pids_limit=64, disk_limit_mb=64, wall_time_ms=15000)
    limits.update(limit_over)
    req = ExecutionRequest(
        run_id=f"rv11-attack-{abs(hash(code)) % 10**10}",
        code=code,
        seed=seed,
        sandbox_image=IMAGE,
        policy=SandboxPolicy(enforce_module_allowlist=guard),
        limits=ResourceLimits(**limits),
    )
    return run_experiment(req)


# --------------------------------------------------------------------------
# 正常数学实验 + 确定性
# --------------------------------------------------------------------------


class TestBenign:
    def test_monte_carlo_pi_passes(self):
        code = (
            "import json, random\n"
            "random.seed(SEED)\n"
            "n = 2000\n"
            "inside = sum(1 for _ in range(n) if random.random()**2 + random.random()**2 <= 1.0)\n"
            'print("RESULT_JSON:", json.dumps({"metrics": {"pi_estimate": 4*inside/n}}))\n'
        )
        result = _run(code)
        assert result.manifest.exec_status == "pass"
        assert result.manifest.exit_code == 0
        assert result.manifest.isolation_mode.value == "container"
        assert result.manifest.image_digest.startswith("sha256:")
        # 隔离控制如实记录（容器后端：2 项 PARTIAL 必须为 False 并带原因）
        controls = result.manifest.isolation_controls_enforced
        assert controls.network_isolation is True
        assert controls.non_root_user is True
        assert controls.tmpfs_workdir is False
        assert controls.disk_limit is False
        assert "PARTIAL" in result.manifest.unenforced_reasons["disk_limit"]

    def test_same_seed_same_output_hash(self):
        code = "import random\nrandom.seed(SEED)\nprint([random.random() for _ in range(20)])\n"
        a = _run(code, seed=7)
        b = _run(code, seed=7)
        assert a.manifest.exec_status == "pass" and b.manifest.exec_status == "pass"
        assert a.manifest.output_hash == b.manifest.output_hash
        assert a.manifest.code_hash == b.manifest.code_hash
        assert a.stdout == b.stdout

    def test_different_seed_different_output(self):
        code = "import random\nrandom.seed(SEED)\nprint([random.random() for _ in range(20)])\n"
        a = _run(code, seed=1)
        b = _run(code, seed=2)
        assert a.stdout != b.stdout


# --------------------------------------------------------------------------
# 守卫层（生产路径恒开）
# --------------------------------------------------------------------------


class TestGuard:
    def test_socket_import_blocked(self):
        result = _run("import socket\nprint('never')\n", guard=True)
        assert result.manifest.exec_status == "fail"
        assert "GUARD_VIOLATION" in result.stderr
        assert "socket" in result.stderr

    def test_open_exec_eval_blocked(self):
        for snippet in ("open('/etc/passwd')\n", "eval('1+1')\n", "exec('x=1')\n"):
            result = _run(snippet, guard=True)
            assert result.manifest.exec_status == "fail", snippet
            assert "GUARD_VIOLATION" in result.stderr

    def test_network_libraries_all_blocked(self):
        for module in ("urllib", "http", "subprocess", "multiprocessing", "ctypes"):
            result = _run(f"import {module}\n", guard=True)
            assert result.manifest.exec_status == "fail", module


# --------------------------------------------------------------------------
# 内核层（guard-off：证明守卫不是唯一防线）
# --------------------------------------------------------------------------


class TestKernelIsolation:
    def test_network_egress_denied(self):
        code = (
            "import socket\n"
            "try:\n"
            "    s = socket.create_connection(('example.com', 80), timeout=3)\n"
            "    print('EGRESS-OK')\n"
            "except OSError as e:\n"
            "    print('EGRESS-BLOCKED', type(e).__name__)\n"
        )
        result = _run(code, guard=False, wall_time_ms=15000)
        assert result.manifest.exec_status == "pass"
        assert "EGRESS-BLOCKED" in result.stdout
        assert "EGRESS-OK" not in result.stdout

    def test_dns_also_dead_without_network(self):
        code = (
            "import socket\n"
            "try:\n"
            "    print('DNS-OK', socket.gethostbyname('example.com'))\n"
            "except OSError as e:\n"
            "    print('DNS-BLOCKED', type(e).__name__)\n"
        )
        result = _run(code, guard=False)
        assert "DNS-BLOCKED" in result.stdout

    def test_docker_socket_not_visible(self):
        code = (
            "import os\n"
            "print('SOCK-EXISTS', os.path.exists('/var/run/docker.sock'))\n"
            "print('RUN-LIST', sorted(os.listdir('/var/run')))\n"
        )
        result = _run(code, guard=False)
        assert "SOCK-EXISTS False" in result.stdout

    def test_runs_as_non_root_uid_10001(self):
        result = _run("import os\nprint('UID', os.getuid())\n", guard=False)
        assert result.manifest.exec_status == "pass"
        assert "UID 10001" in result.stdout

    def test_no_host_env_secrets(self):
        """容器 env 必须是固定字面量：**宿主真实存在的**任何 secret 类变量名
        （KEY/SECRET/TOKEN/PASSWORD/CREDENTIAL）与宿主专属变量都不得出现。"""
        import json as _json

        container_keys: set[str] | None = None
        code = (
            "import os, json\n"
            "print('ENV-JSON', json.dumps(sorted(os.environ.keys())))\n"
        )
        result = _run(code, guard=False)
        assert result.manifest.exec_status == "pass"
        for line in result.stdout.splitlines():
            if line.startswith("ENV-JSON "):
                container_keys = set(_json.loads(line[len("ENV-JSON "):]))
        assert container_keys is not None

        host = os.environ
        host_secret_names = {
            k
            for k in host
            if any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"))
        }
        leaked_secrets = host_secret_names & container_keys
        assert not leaked_secrets, f"宿主 secret 变量名泄漏进容器: {sorted(leaked_secrets)}"
        host_only = {"APPDATA", "ProgramData", "USERNAME", "USERPROFILE", "SystemRoot", "COMPUTERNAME", "PATH_EXT"}
        leaked_host = (host_only & container_keys) | (
            {k for k in host if k.startswith("WORKBUDDY")} & container_keys
        )
        assert not leaked_host, f"宿主专属变量泄漏进容器: {sorted(leaked_host)}"
        # 固定注入集必须存在（runner 契约的一部分）
        assert {"HOME", "TMPDIR", "LANG", "PYTHONIOENCODING"} <= container_keys

    def test_read_only_rootfs(self):
        code = (
            "try:\n"
            "    open('/etc/pwned','w').write('x')\n"
            "    print('ROOTFS-WRITABLE')\n"
            "except OSError as e:\n"
            "    print('ROOTFS-READONLY', type(e).__name__)\n"
        )
        result = _run(code, guard=False)
        assert "ROOTFS-READONLY" in result.stdout
        assert "ROOTFS-WRITABLE" not in result.stdout

    def test_escape_attempt_beyond_workdir_fails(self):
        code = (
            "try:\n"
            "    open('/work/../../escaped.txt','w').write('x')\n"
            "    print('ESCAPED')\n"
            "except OSError as e:\n"
            "    print('TRAPPED', type(e).__name__)\n"
        )
        result = _run(code, guard=False)
        assert "TRAPPED" in result.stdout
        assert "ESCAPED" not in result.stdout

    def test_fork_bomb_capped_by_pids_limit(self):
        code = (
            "import os\n"
            "n = 0\n"
            "try:\n"
            "    while True:\n"
            "        pid = os.fork()\n"
            "        if pid == 0:\n"
            "            os._exit(0)\n"
            "        n += 1\n"
            "except OSError as e:\n"
            "    print('FORK-CAPPED', n, type(e).__name__)\n"
        )
        result = _run(code, guard=False, pids_limit=32)
        assert result.manifest.exec_status == "pass"
        assert "FORK-CAPPED" in result.stdout
        assert "FORK-CAPPED 0 " not in result.stdout  # 确实 fork 过若干个后才被 cap

    def test_memory_bomb_killed_by_cgroup(self):
        code = "x = bytearray(400 * 1024 * 1024)\nprint('ALLOC-OK')\n"
        result = _run(code, guard=False, memory_limit_mb=128)
        assert result.manifest.exec_status in ("fail", "timeout")
        assert "ALLOC-OK" not in result.stdout

    def test_wall_clock_timeout_kills(self):
        result = _run("while True:\n    pass\n", guard=False, wall_time_ms=3000)
        assert result.manifest.exec_status == "timeout"
        assert result.manifest.timed_out is True
        assert result.manifest.exit_code != 0

    def test_output_cap_truncates(self):
        code = "for i in range(200000):\n    print('X' * 64)\n"
        result = _run(code, guard=False, output_limit_bytes=64 * 1024, wall_time_ms=20000)
        assert result.manifest.truncation.truncated is True
        assert len(result.stdout) <= 64 * 1024 + 1024

    def test_tmpfs_disk_limit_enospc(self):
        code = (
            "try:\n"
            "    f = open('/tmp/big.bin', 'wb')\n"
            "    f.write(b'0' * (128 * 1024 * 1024))\n"
            "    f.close()\n"
            "    print('WROTE-128MB')\n"
            "except OSError as e:\n"
            "    print('ENOSPC', type(e).__name__)\n"
        )
        result = _run(code, guard=False, disk_limit_mb=64)
        assert "ENOSPC" in result.stdout
        assert "WROTE-128MB" not in result.stdout


# --------------------------------------------------------------------------
# argv 层（无需容器）
# --------------------------------------------------------------------------


class TestArgvContract:
    def test_argv_rejects_foreign_mount(self):
        from sandbox.backends.container import ContainerBackend
        from sandbox.models import ExecutionRequest

        backend = ContainerBackend()
        argv = ["docker", "run", "-v", "/host/secret:/steal", "img"]
        with pytest.raises(RuntimeError):
            backend.assert_argv_safe(argv)

    def test_argv_rejects_docker_socket(self):
        from sandbox.backends.container import ContainerBackend

        backend = ContainerBackend()
        argv = ["docker", "run", "-v", "/var/run/docker.sock:/var/run/docker.sock", "img"]
        with pytest.raises(RuntimeError):
            backend.assert_argv_safe(argv)

    def test_built_argv_has_only_work_bind(self):
        from sandbox.backends.container import CONTAINER_WORKDIR, ContainerBackend
        from sandbox.workspace import RunWorkspace

        backend = ContainerBackend(image=IMAGE)
        req = ExecutionRequest(run_id="argv-probe", code="print(1)\n", sandbox_image=IMAGE)
        ws = RunWorkspace.create(None, "argv-probe", 64)
        try:
            argv = backend.build_argv(req, ws, "ma-sandbox-probe", image=IMAGE)
            backend.assert_argv_safe(argv)  # 不抛 = 只有 /work bind
            joined = " ".join(argv)
            assert "--network none" in joined
            assert "--user 10001:10001" in joined
            assert "--read-only" in joined
            assert f"-v" in joined and CONTAINER_WORKDIR in joined
        finally:
            ws.cleanup()
