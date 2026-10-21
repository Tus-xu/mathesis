"""R21 测试共享夹具：合成 spec 构造器 + 可编排故障的 8201 fake 引擎。

spec 为合成内容（零真实教材内容入测试）；locator/page_refs/学情引用形态与真实链路一致。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.reliability.presenton_client import EngineRejected, PresentonUnavailable

LOC = "textbook_v2/locator/v1/source/50000000-0000-0000-0000-0000000000a1/section/50000000-0000-0000-0000-0000000000a3/chunk/{i}"
PROFILE = ("learning_profile/v1/fixture-lp-test-01/sample/32/window/2026-08-25..2026-09-08"
           "/coverage/0.62/confidence/0.71")

SLIDE_TYPES = ["title", "introduction", "concept", "definition", "worked-example",
               "practice", "concept", "summary"]


def build_spec(*, n_slides: int = 8, with_assets: bool = False, bad: dict | None = None) -> dict[str, Any]:
    """bad：制造闸门违规的开关（page_refs/locator/profile/pages/not_approved）。"""
    slides = []
    for i in range(n_slides):
        st = SLIDE_TYPES[i % len(SLIDE_TYPES)]
        refs = [LOC.format(i=i + 1)]
        if i in (1, 4):
            refs.append(PROFILE)
        slides.append({
            "id": f"slide-{i + 1:02d}",
            "type": st,
            "teaching_goal": f"目标 {i + 1}",
            "title": f"合成标题 {i + 1}",
            "content": "要点一\n要点二\n- 列表项 A\n- 列表项 B",
            "source_refs": refs,
            "assets": ["asset-fig-1"] if (with_assets and i == 2) else [],
            "visual_intent": "",
            "layout_hint": st,
            "speaker_note": f"备注 {i + 1}",
            "revision": 0,
            "status": "draft",
            "qa_status": "pending",
        })
    spec = {
        "schema_version": "2.0.0",
        "id": "d00d0000-0000-0000-0000-0000000000c2",
        "revision": 1,
        "metadata": {"title": "合成集合教学蓝图", "grade": "高一", "duration_minutes": 30,
                     "language": "zh-CN", "course_context_ref": "c0ffee00-0000-0000-0000-0000000000b1"},
        "textbook_sources": [{
            "textbook_id": "50000000-0000-0000-0000-0000000000a1",
            "section_id": "50000000-0000-0000-0000-0000000000a3",
            "page_refs": [],
            "chunk_refs": [LOC.format(i=1)],
            "locator": LOC.format(i=1),
        }],
        "teaching_strategy": {"intro": "理解集合", "concept_progression": ["集合与元素"],
                              "derivation_chain": [], "checks_for_understanding": ["思考"]},
        "outline": [{"id": "stage-01", "goal": "导入", "slide_ids": ["slide-01"]}],
        "selected_examples": [{"problem_id": "例1", "usage": "guided"}],
        "theme": None,
        "slides": slides,
        "assets": ([{
            "id": "asset-fig-1", "kind": "MATH_RENDER",
            "object_key": f"pps2/assets/{'a' * 64}", "sha256": "a" * 64,
            "mime_type": "image/png", "size_bytes": 1024, "width": 800, "height": 600,
            "source_ref": None, "rights_status": "STAGED", "rights_evidence_ref": "fixture",
            "preserve_original": True, "created_by": None, "created_at": None,
            "transform_history": [],
        }] if with_assets else []),
        "speaker_notes": {},
        "change_log": [],
        "projection": {
            "schema_version": "1.0.0", "projector": "teacher_prep.specs.v1/fixture-r21",
            "approved_blueprint_id": "c0ffee00-0000-0000-0000-0000000000b1",
            "approved_blueprint_revision": 3,
            "context_snapshot_hash": "b" * 64, "prompt_release_id": "fixture",
            "ai_run_id": "11112222-3333-4444-5555-666677778888", "model": "deterministic-fake",
            "run_hash": "c" * 64, "body_hash": "d" * 64, "projected_at": "2026-09-11T00:00:00Z",
            "fixture": True,
        },
    }
    if bad:
        if bad.get("page_refs"):
            spec["textbook_sources"][0]["page_refs"] = ["p12"]
        if bad.get("locator"):
            spec["textbook_sources"][0]["locator"] = "第 12 页"
            spec["textbook_sources"][0]["chunk_refs"] = []
        if bad.get("profile"):
            spec["slides"][1]["source_refs"] = [LOC.format(i=2), "learning_profile/v1/fixture-x"]
        if bad.get("pages"):
            base = build_spec(n_slides=int(bad["pages"]), with_assets=with_assets)
            base["projection"] = spec["projection"]
            return base
        if bad.get("not_approved"):
            spec.pop("projection")
        if bad.get("asset_ref_missing"):
            spec["slides"][2]["assets"] = ["ghost-asset"]
    return spec


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Fake8201:
    """可编排故障的 8201 fake：内容寻址行为与真引擎一致（ref=sha256(bytes)）。

    fail 页模式：transient（一次 PresentonUnavailable 后自愈）/ qa（一次 QA 不合格，
    永久到重试成功由显式清除控制）/ reject（EngineRejected，永久）。
    """

    def __init__(self, *, version: str = "r21-engine-1.0.0", n_pages_default: int = 8):
        self.version = version
        self.artifacts: dict[str, bytes] = {}
        self.fail: dict[int, str] = {}
        self.fail_once: set[int] = set()
        self.render_calls: dict[int, int] = {}
        self.assemble_calls = 0

    async def probe(self) -> None:
        return None

    async def engine_version(self) -> str:
        return self.version

    async def render_page(self, body: dict, *, timeout_s: float = 0) -> dict:
        pno = int(body["page_no"])
        self.render_calls[pno] = self.render_calls.get(pno, 0) + 1
        mode = self.fail.get(pno)
        if mode and pno in self.fail_once:
            self.fail_once.discard(pno)
            if mode == "transient":
                raise PresentonUnavailable("fake 8201 down")
            if mode == "reject":
                raise EngineRejected("fake reject", status_code=422, detail="bad-contract")
        frag = f"frag:{pno}:{body['slide']['id']}".encode()
        frag_ref = sha(frag)
        self.artifacts[frag_ref] = frag
        png = f"png:{pno}".encode()
        png_ref = sha(png)
        self.artifacts[png_ref] = png
        if mode == "qa":  # 永久 QA 不合格（坏页场景）
            return {"ok": False, "content_sha256": sha(f"content:{pno}".encode()),
                    "fragment_ref": frag_ref, "png_ref": png_ref, "qa": {"overflow": True},
                    "errors": ["overflow:title"], "elapsed_ms": 3,
                    "fragment_size": len(frag), "png_size": len(png), "engine": self.version}
        return {"ok": True, "content_sha256": sha(f"content:{pno}".encode()),
                "fragment_ref": frag_ref, "png_ref": png_ref,
                "fragment_sha256": frag_ref, "png_sha256": png_ref,
                "fragment_size": len(frag), "png_size": len(png),
                "qa": {"overflow": False, "overlap": False, "truncated_chars": 0,
                       "formula_ok": True, "figure": "absent", "render": "ok-std42.0",
                       "font_fallbacks": {"∈": "Segoe UI Symbol"}, "font_missing": [],
                       "math_chars": ["∈"]},
                "errors": [], "elapsed_ms": 3, "engine": self.version}

    async def artifact_meta(self, ref: str) -> dict | None:
        if ref in self.artifacts:
            return {"ref": ref, "sha256": sha(self.artifacts[ref]), "size_bytes": len(self.artifacts[ref])}
        return None

    async def artifact_bytes(self, ref: str) -> bytes:
        if ref in self.artifacts:
            return self.artifacts[ref]
        raise EngineRejected("artifact missing", status_code=404, detail=ref)

    async def assemble(self, body: dict, *, timeout_s: float = 0) -> dict:
        self.assemble_calls += 1
        pages = sorted(body["pages"], key=lambda p: p["page_no"])
        blob = ("DECK:" + "|".join(p["page_ref"] for p in pages)).encode()
        deck_ref = sha(blob)
        self.artifacts[deck_ref] = blob
        cs = ("CS:" + deck_ref).encode()
        cs_ref = sha(cs)
        self.artifacts[cs_ref] = cs
        return {"deck_key": sha(json.dumps({"spec": body["deck_spec_sha256"],
                                            "pages": [p["page_ref"] for p in pages]},
                                           sort_keys=True).encode()),
                "deck_ref": deck_ref, "deck_sha256": sha(blob), "size_bytes": len(blob),
                "page_count": len(pages), "contact_sheet_ref": cs_ref,
                "openable": {"opened": True, "slide_count": len(pages), "rendered_pngs": len(pages)},
                "elapsed_ms": 9, "engine": self.version}
