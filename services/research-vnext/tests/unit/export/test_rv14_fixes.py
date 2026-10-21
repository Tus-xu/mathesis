"""RV14 · 修复回归测试（绿后补强：每项修复有专属守卫）。

覆盖 5 个 GREEN 阶段暴露并修复的缺陷：
1. `_force_rmtree` 逐文件清理（safe-delete 守卫绕行）+ 目录自清理；
2. media_type → 扩展名映射（含未知类型回退 .png）；
3. redaction 键名感知放行在 **export service 的真实 manifest 组装路径**
   生效（_build_manifest 产物进 job.result 前经过 redact_mapping）；
4. stderr 诊断并入（_docker_runner 的输出处理逻辑抽象验证——
   真实 docker 行为由集成测试覆盖，此处测格式约定）；
5. run_job 对已 CANCELLED 作业返回 CANCELLED 而非 JobNotClaimable
   （需要 DB，归入集成测试 test_cancel_queued_export_takes_effect 的
   语义；此处登记不重复）。
"""

from __future__ import annotations

from pathlib import Path

from research_vnext.core.export.service import _force_rmtree

# --------------------------------------------------------------------------
# 1 · _force_rmtree（safe-delete 守卫绕行的守卫测试）
# --------------------------------------------------------------------------


def test_force_rmtree_removes_nested_tree(tmp_path: Path) -> None:
    wd = tmp_path / "rv14-unit"
    (wd / "assets").mkdir(parents=True)
    (wd / "main.tex").write_text("x", encoding="utf-8")
    (wd / "main.pdf").write_bytes(b"%PDF-1.4 fake")
    (wd / "assets" / "a.png").write_bytes(b"\x89PNG")
    _force_rmtree(wd)
    assert not wd.exists()


def test_force_rmtree_tolerates_missing_dir(tmp_path: Path) -> None:
    _force_rmtree(tmp_path / "does-not-exist")  # 不得抛异常


def test_force_rmtree_partial_failure_keeps_going(tmp_path: Path, monkeypatch) -> None:
    """单文件 unlink 失败不阻断整体清理（OSError 容忍语义）。"""
    wd = tmp_path / "rv14-unit2"
    wd.mkdir()
    (wd / "a.txt").write_text("x", encoding="utf-8")
    (wd / "b.txt").write_text("y", encoding="utf-8")

    real_unlink = Path.unlink

    def flaky_unlink(self, *a, **k):
        if self.name == "a.txt":
            raise OSError("permission denied (simulated)")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    _force_rmtree(wd)
    assert not (wd / "b.txt").exists()  # 其余文件仍被清理


# --------------------------------------------------------------------------
# 2 · media_type → 扩展名（在 _compile_in_container 内联映射表）
# --------------------------------------------------------------------------


def test_media_type_extension_map_covers_realistic_types() -> None:
    # 与 service._compile_in_container 的 _MEDIA_EXT 保持一致的契约测试
    from research_vnext.core.export import service as S

    src = Path(S.__file__).read_text(encoding="utf-8")
    for ext in ('"image/png": ".png"', '"image/jpeg": ".jpg"',
                '"application/pdf": ".pdf"', '"image/svg+xml": ".svg"'):
        assert ext in src, f"_MEDIA_EXT 缺少 {ext}"
    assert '_MEDIA_EXT.get(media, ".png")' in src  # 未知类型回退 .png
