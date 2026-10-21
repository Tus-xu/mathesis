"""响应壳与 catalog/decks/plans/grading 契约（移植自 v3MockContract.test.ts）。

断言蓝本：notes/B5-notes.md §1.1（响应壳 #1-5）、§1.4（列表 #25-31）、§1.5（枚举）、§1.8（行为 #67-70）。
前端契约测试不改断言即可切换 = 本文件全绿是底线（README 纪律 5）。
"""
from __future__ import annotations

import pytest

from conftest import _auth

pytestmark = pytest.mark.usefixtures("app")


# ---------------- 响应壳 ----------------


async def test_today_envelope_shape(client, teacher_token):
    """MC:77-86 — today 契约：壳 + teacher/schedule/todos/class_brief。"""
    r = await client.get("/teacher-v3/today", headers=_auth(teacher_token))
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    # 后端统一补 request_id，且与 x-request-id 响应头一致（#3/#4）
    assert body["request_id"] and body["request_id"] == r.headers.get("x-request-id")
    d = body["data"]
    assert d["teacher"]["name"]
    assert len(d["schedule"]) > 0
    assert any(s["status"] == "next" for s in d["schedule"])
    assert all(t["kind"] in ("grade", "prep", "review", "meeting") for t in d["todos"])
    assert all(c["trend"] and isinstance(c["avg"], (int, float)) for c in d["class_brief"])


async def test_lists_are_enveloped_not_bare(client, teacher_token):
    """MC:88-94 / #25 — 列表端点一律 {items:[...]}，禁止裸数组。"""
    for path in (
        "/teacher-v3/classes",
        "/teacher-v3/decks",
        "/teacher-v3/plans",
        "/teacher-v3/grading/assignments",
        "/teacher-v3/folders",
        "/teacher-v3/resources",
        "/teacher-v3/recipes",
        "/teacher-v3/draw/library",
    ):
        r = await client.get(path, headers=_auth(teacher_token))
        assert r.status_code == 200, path
        data = r.json()["data"]
        assert isinstance(data, dict) and isinstance(data.get("items"), list), f"{path} 必须返回 {{items}} 包装"


async def test_tasks_shape_running_count(client, teacher_token):
    """MC:96-101 / #28 — tasks → {items, running}，含进度。"""
    r = await client.get("/teacher-v3/tasks", headers=_auth(teacher_token))
    assert r.status_code == 200
    d = r.json()["data"]
    assert isinstance(d["running"], int)
    for t in d["items"]:
        assert isinstance(t["progress"], (int, float))
        assert t["status"] in ("queued", "running", "succeeded", "failed")  # #35
        assert t["task_id"]


async def test_error_envelope_not_found(client, teacher_token):
    """#21/#23 — 删除不存在题目 → 404 + 40402，data:null。"""
    r = await client.delete("/teacher-v3/quiz/questions/not-exist-x", headers=_auth(teacher_token))
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 40402
    assert body["data"] is None
    assert body["message"]


# ---------------- catalog 域行为 ----------------


async def test_quiz_questions_enums_and_total(client, teacher_token):
    """MC:122-132 / #26/#32-34 — 枚举合法 + total==items.length（无分页契约）。"""
    r = await client.get("/teacher-v3/quiz/questions", headers=_auth(teacher_token))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == len(d["items"])
    assert all(q["q_type"] in ("fill", "choice", "solve", "image") for q in d["items"])
    assert all(q["difficulty"] in ("easy", "medium", "hard") for q in d["items"])
    assert all(q["source"] in ("校本", "区库", "拍照入库", "自编", "AI配题") for q in d["items"])
    img = next((q for q in d["items"] if q["q_type"] == "image"), None)
    if img is not None:  # 种子含图片题型时必须带 stem_image（#32）
        assert isinstance(img["stem_image"], str) and img["stem_image"]


async def test_quiz_filter_server_side(client, teacher_token):
    """#27 — 服务端筛选：source=拍照入库 过滤。"""
    r = await client.get(
        "/teacher-v3/quiz/questions", params={"source": "拍照入库"}, headers=_auth(teacher_token)
    )
    assert r.status_code == 200
    assert all(q["source"] == "拍照入库" for q in r.json()["data"]["items"])


async def test_kp_tree_three_levels(client, teacher_token):
    """MC:134-144 / P4 — 分类树：模块→章→知识点（叶挂 kp_codes）。"""
    r = await client.get("/teacher-v3/quiz/kp-tree", headers=_auth(teacher_token))
    assert r.status_code == 200
    tree = r.json()["data"]["tree"]
    assert len(tree) > 0
    assert "children" in tree[0]
    leaf = tree[0]["children"][0]["children"][0]
    assert "name" in leaf and "kp_codes" in leaf


async def test_quiz_create_then_delete(client, teacher_token):
    """MC:169-190 / #67 — 自编题录入（source='自编'）→ 删除 → 404。"""
    created = await client.post(
        "/teacher-v3/quiz/questions",
        json={
            "kp_code": "YD-01",
            "kp_name": "椭圆标准方程",
            "q_type": "solve",
            "difficulty": "easy",
            "stem_latex": "要删除的测试题",
            "answer": "略",
        },
        headers=_auth(teacher_token),
    )
    assert created.status_code == 200
    q = created.json()["data"]
    assert q["source"] == "自编"
    qid = q["id"]

    before = (await client.get("/teacher-v3/quiz/questions", headers=_auth(teacher_token))).json()["data"]["items"]
    deleted = await client.delete(f"/teacher-v3/quiz/questions/{qid}", headers=_auth(teacher_token))
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"ok": True}
    after = (await client.get("/teacher-v3/quiz/questions", headers=_auth(teacher_token))).json()["data"]["items"]
    assert len(after) == len(before) - 1
    assert all(x["id"] != qid for x in after)


async def test_scan_import_persists_image_question(client, teacher_token):
    """MC:146-156 / #67 — 扫描入库：POST 后回读可见，q_type=image + source=拍照入库。"""
    before = (await client.get("/teacher-v3/quiz/questions", headers=_auth(teacher_token))).json()["data"]["items"]
    imp = await client.post(
        "/teacher-v3/quiz/scan-import",
        json={
            "src": "data:image/png;base64,AAAA",
            "kp_code": "LT-05",
            "kp_name": "立体截面",
            "as_image": True,
            "kp_path": ["立体几何", "截面"],
        },
        headers=_auth(teacher_token),
    )
    assert imp.status_code == 200
    d = imp.json()["data"]
    assert d["q_type"] == "image" and d["source"] == "拍照入库" and d["kp_code"] == "LT-05"
    after = (await client.get("/teacher-v3/quiz/questions", headers=_auth(teacher_token))).json()["data"]["items"]
    assert len(after) == len(before) + 1
    assert any(x["id"] == d["id"] and x["q_type"] == "image" for x in after)


async def test_textbook_chapters_provenance(client, teacher_token):
    """MC:192-207 / #47 — 教材目录 provenance 与真实章节。"""
    got = await client.get("/teacher-v3/textbook-chapters", headers=_auth(teacher_token))
    assert got.status_code == 200
    d = got.json()["data"]
    assert d["provenance"] in ("preset", "knowledge_base")
    rj = next((t for t in d["textbooks"] if t["name"] == "人教A版（2019）"), None)
    if rj is not None:
        assert rj["editable"] is True
        assert any("椭圆" in c["path"] for c in rj["chapters"])


async def test_kp_suggest_shape(client, teacher_token):
    """API:132-135 — kp-suggest：suggestion{code,path,name,confidence} + alternates。"""
    r = await client.post(
        "/teacher-v3/quiz/kp-suggest",
        json={"stem": "已知椭圆的离心率为二分之一，求标准方程"},
        headers=_auth(teacher_token),
    )
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["suggestion"]["code"] and d["suggestion"]["path"]
    assert isinstance(d["alternates"], list)


# ---------------- decks 域 ----------------


async def test_deck_detail_structured_elements(client, teacher_token):
    """MC:210-221 / #38 — deck 详情：结构化元素（8 类），禁截图充数（红线 2）。"""
    lst = await client.get("/teacher-v3/decks", headers=_auth(teacher_token))
    assert lst.status_code == 200
    items = lst.json()["data"]["items"]
    assert all(isinstance(x["slide_count"], int) for x in items)
    if not items:
        pytest.skip("种子 deck 为空（B2/B4 种子数据未入库）——列表契约已验")
    deck_id = items[0]["id"]
    detail = await client.get(f"/teacher-v3/decks/{deck_id}", headers=_auth(teacher_token))
    assert detail.status_code == 200
    deck = detail.json()["data"]
    element_types = {"text", "formula", "geometry", "functionPlot", "dynamicDemo", "image", "anchorPhoto", "pageNo"}
    for s in deck["slides"]:
        assert all(e["type"] in element_types for e in s["elements"])


async def test_deck_patch_title(client, teacher_token):
    """MC:223-239 — PATCH 改标题回读一致。"""
    lst = (await client.get("/teacher-v3/decks", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子 deck 为空")
    deck_id = lst[0]["id"]
    patched = await client.patch(
        f"/teacher-v3/decks/{deck_id}", json={"title": "椭圆（改）"}, headers=_auth(teacher_token)
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["title"] == "椭圆（改）"


async def test_deck_export_returns_task_id(client, teacher_token):
    """MC:241-244 / #70 — 导出：异步 {task_id}，不伪装完成。"""
    lst = (await client.get("/teacher-v3/decks", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子 deck 为空")
    deck_id = lst[0]["id"]
    r = await client.post(
        f"/teacher-v3/decks/{deck_id}/export", json={"format": "pptx"}, headers=_auth(teacher_token)
    )
    assert r.status_code == 200
    task_id = r.json()["data"]["task_id"]
    assert task_id


# ---------------- plans 域 ----------------


async def test_plan_ten_boards_and_cliche(client, teacher_token):
    """MC:247-264 / #52 — 十板块详情：三栏 + 挂例题 + 反套话。"""
    lst = (await client.get("/teacher-v3/plans", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子 plan 为空")
    plan_id = lst[0]["id"]
    r = await client.get(f"/teacher-v3/plans/{plan_id}", headers=_auth(teacher_token))
    assert r.status_code == 200
    plan = r.json()["data"]
    assert len(plan["sections"]) == 10  # V3_TEN_BOARDS 全量
    first = plan["sections"][0]
    assert {"teacher_activity", "student_activity", "design_intent"} <= set(first)
    with_examples = [s for s in plan["sections"] if s.get("examples")]
    if with_examples:
        ex = with_examples[0]["examples"][0]
        assert {"label", "stem_latex", "answer"} <= set(ex)
    objectives = next((s for s in plan["sections"] if s["name"] == "教学目标"), None)
    if objectives is not None:
        assert objectives.get("cliche") is True
        assert len(objectives.get("cliche_hits", [])) >= 1


async def test_push_to_deck_creates_real_row(client, teacher_token):
    """MC:266-273 / #69 — 推送课件：新 deck source=lesson-push。"""
    lst = (await client.get("/teacher-v3/plans", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子 plan 为空")
    r = await client.post(
        f"/teacher-v3/plans/{lst[0]['id']}/push-to-deck",
        json={"template_id": "tpl-academic-blue"},
        headers=_auth(teacher_token),
    )
    assert r.status_code == 200
    deck_id = r.json()["data"]["deck_id"]
    assert deck_id
    decks = (await client.get("/teacher-v3/decks", headers=_auth(teacher_token))).json()["data"]["items"]
    pushed = next((d for d in decks if d["id"] == deck_id), None)
    assert pushed is not None and pushed["source"] == "lesson-push"


# ---------------- grading 域 ----------------


async def test_grading_detail_clusters_and_tiers(client, teacher_token):
    """MC:276-304 — 批改详情：聚类枚举 + 原图归一化区域 + tiers 顺序。"""
    lst = (await client.get("/teacher-v3/grading/assignments", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子作业为空")
    r = await client.get(f"/teacher-v3/grading/assignments/{lst[0]['id']}", headers=_auth(teacher_token))
    assert r.status_code == 200
    g = r.json()["data"]
    for q in g.get("questions", []):
        for c in q.get("clusters", []):
            assert c["kind"] in ("correct", "partial", "wrong", "blank")
            for s in c.get("sample", []):
                reg = s["photo_region"]
                assert 0 <= reg["x"] and reg["x"] + reg["w"] <= 1.000001
                assert all(st["status"] in ("ok", "ai-flag", "corrected") for st in s["recognized_steps"])
    if g.get("tiers"):
        assert [t["tier"] for t in g["tiers"]] == ["A", "B", "C"]


async def test_grading_cluster_confirm_writes_back(client, teacher_token):
    """MC:297-304 — 聚类评语审定写回 sample.feedback（幂等键语义见 test_idempotency）。"""
    lst = (await client.get("/teacher-v3/grading/assignments", headers=_auth(teacher_token))).json()["data"]["items"]
    if not lst:
        pytest.skip("种子作业为空")
    detail = (
        await client.get(f"/teacher-v3/grading/assignments/{lst[0]['id']}", headers=_auth(teacher_token))
    ).json()["data"]
    cluster = next((c for q in detail["questions"] for c in q["clusters"]), None)
    if cluster is None:
        pytest.skip("种子作业无聚类")
    fb = "审定评语：注意不等号方向"
    r = await client.post(
        f"/teacher-v3/grading/assignments/{lst[0]['id']}/clusters/{cluster['id']}/confirm",
        json={"feedback": fb},
        headers=_auth(teacher_token),
    )
    assert r.status_code == 200
    after = (
        await client.get(f"/teacher-v3/grading/assignments/{lst[0]['id']}", headers=_auth(teacher_token))
    ).json()["data"]
    c2 = next((c for q in after["questions"] for c in q["clusters"] if c["id"] == cluster["id"]), None)
    assert c2 is not None
    assert any(s.get("feedback") == fb for s in c2["sample"])


# ---------------- recognition 域 ----------------


async def test_rebuild_candidates_validation_gate(client, teacher_token):
    """MC:307-315 / #68 — 构造校验门：未过者不允许上屏。"""
    r = await client.post(
        "/teacher-v3/recognition/rebuild",
        json={"photo_region": {"x": 0.28, "y": 0.62, "w": 0.45, "h": 0.3}},
        headers=_auth(teacher_token),
    )
    assert r.status_code == 200
    cands = r.json()["data"]["candidates"]
    assert len(cands) >= 1
    for c in cands:
        assert isinstance(c["passed_validation"], bool)
        if not c["passed_validation"]:
            assert c["match_note"]
