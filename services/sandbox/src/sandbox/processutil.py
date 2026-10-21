"""Process-level helpers shared by the local backend (and used to reap containers).

Isolation-relevant design notes:

* ``kill_tree`` is used for the wall-clock timeout. On POSIX the child is started
  in its own session so ``killpg`` reaches grandchildren; on Windows
  ``taskkill /T /F`` is used. A plain ``Popen.kill()`` would leave grandchildren
  running -- which for a fork-bomb-style payload means "timeout enforced" would be
  a false statement.
* Nothing here is a security control. It is process bookkeeping.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def kill_tree(proc: subprocess.Popen) -> None:
    """Best-effort termination of a process and its descendants."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except Exception:  # noqa: BLE001
            proc.kill()
    else:  # pragma: no cover - POSIX path is not exercised on this Windows host
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            proc.kill()
    try:
        proc.wait(timeout=15)
    except Exception:  # noqa: BLE001
        pass


def popen_kwargs_new_group() -> dict[str, object]:
    """Start the child in its own group/session so kill_tree can reach descendants."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def dir_size_bytes(root: Path, exclude: tuple[str, ...] = ()) -> int:
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(relative == item or relative.startswith(item + "/") for item in exclude):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def rmtree_quiet(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
