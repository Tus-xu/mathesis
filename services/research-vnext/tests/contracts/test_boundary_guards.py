"""RV02 · 施工边界守卫（P00 registry 对 8300 的四条硬约束）。

1. 不 import Teacher domain；
2. 不复用 Teacher DTO/schema；
3. 不读 Teacher 表；
4. 不自建第二套账号库。
附加：端口/前缀固化、禁止 SQLite 冒充生产证据。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
PKG = SERVICE_ROOT / "research_vnext"

FORBIDDEN_IMPORTS = (
    "app.domains",
    "app.teacher_prep",
    "app.ppt_v2",
    "app.textbook_v2",
    "app.export",
    "teacher_platform",
    "ppt_studio_v2",
    "services.api.app.domains.research",
)

# `platform_contracts` 是**共享契约**，允许在**测试期**导入做等价性校验，
# 但生产代码（research_vnext/**）不得运行时依赖。
FORBIDDEN_IN_PROD = FORBIDDEN_IMPORTS + ("platform_contracts",)


def _py_files(root: Path):
    return [p for p in root.rglob("*.py") if "__pycache__" not in str(p)]


def _code_only(text: str) -> str:
    """剥掉注释与字符串字面量，只留可执行代码。

    动机（2026-09-13 实测）：`test_no_second_account_store` 的正则
    `class\\s+\\w*(user|account|credential)\\w*\\s*\\(` 会被**文档里**的讨论误伤 ——
    `llm/gateway.py` 在 docstring 里解释"本平台背后有两个**账号**，
    模型授权按账号隔离"，于是这个"不得自建账号库"的守卫把一个纯文档提及
    报成了违规。

    一个会因为**注释措辞**而失败的守卫不是严格，而是**脆弱**：它逼着后来的人
    为了过测试去删掉有价值的设计说明。因此这里先把注释与字符串剥掉再判定 ——
    判定强度不变（真正的 `class Account...` 仍然会被抓到），噪音消失。
    """
    import io
    import tokenize

    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - 语法异常时退回原文
        return text
    # 用**单个空格**拼接：`class` 与类名之间必须仍然相邻，
    # 否则 `class\s+\w+account\w*\s*\(` 这类正则会被 token 之间的换行打断，
    # 守卫就变成了永真（这一条是被 meta-test 抓出来的）。
    return " ".join(out)


def test_production_code_has_no_teacher_import() -> None:
    bad = []
    for f in _py_files(PKG):
        text = f.read_text(encoding="utf-8")
        for token in FORBIDDEN_IN_PROD:
            if re.search(rf"^\s*(from|import)\s+.*{re.escape(token)}", text, re.M):
                bad.append((str(f.relative_to(SERVICE_ROOT)), token))
    assert not bad, f"生产代码出现禁止的跨域 import: {bad}"


def test_no_teacher_table_reads() -> None:
    """SQL/ORM 层不得出现 Teacher 表名。"""
    forbidden = ("teaching_session", "teacher_deck", "deck", "lesson_plan", "textbook_page")
    bad = []
    for f in _py_files(PKG):
        text = f.read_text(encoding="utf-8").lower()
        for t in forbidden:
            if re.search(rf"\b{t}\b", text) and "research_session" not in t:
                bad.append((str(f.relative_to(SERVICE_ROOT)), t))
    assert not bad, f"疑似读取 Teacher 表: {bad}"


#: "疑似自建账号库"的判定正则。
#: 注意 `\s*` → ` ?`：`_code_only` 用**单空格**拼接 token，
#: 因此 `class AccountRow(Base)` 会变成 `class AccountRow ( Base ) :`。
#: 正则必须与这个形态匹配，否则守卫会退化成永真（被 meta-test 抓过一次）。
ACCOUNT_CLASS_RE = re.compile(
    r"class\s+(\w*(?:user|account|credential)\w*)\s*\( ?", re.IGNORECASE
)

#: **已审阅的豁免**：名字里含 account/credential 但确实**不是**账号库的类。
#:
#: 刻意做成"显式白名单 + 元测试"而不是把正则放宽：
#: 放宽会让守卫失去意义；白名单要求每个新增豁免都被**写下来并审阅**。
#: 并有 `test_reviewed_exemptions_are_all_still_used` 防止它积累死条目。
REVIEWED_CLASS_EXEMPTIONS: dict[str, str] = {
    # 描述"供应商账号↔模型授权是否配对"的异常类型。
    # 它不存账号、不校验口令、不建表；只是把一个配置错误分类清楚。
    "ModelAccountMismatch": "供应商账号↔模型配对错误的异常类型，非账号存储",
}


def _account_class_hits(text: str) -> list[str]:
    return [m.group(1) for m in ACCOUNT_CLASS_RE.finditer(text)]


def test_no_second_account_store() -> None:
    """不自建账号库：不得出现 user/account 表或本地注册。

    **2026-09-13 有意更新**：改为只扫**代码**（剥掉注释/字符串），并加入
    显式豁免表。原实现会被"文档里解释供应商账号隔离"误伤 —— `llm/gateway.py`
    的 docstring 说明了本平台背后有两个供应商账号、模型授权按账号隔离
    （LEAD ADDENDUM 要求记录的事实），于是这条守卫把一个纯文档提及报成违规。

    断言强度未降低：真正的 `class AccountRow(...)` / `class User(...)` /
    `create_user` / `password_hash` 仍然会被抓 —— 由 meta-test 保证，不靠信任。
    """
    bad: list[tuple[str, str]] = []
    for f in _py_files(PKG):
        text = _code_only(f.read_text(encoding="utf-8"))
        lowered = text.lower()
        name = str(f.relative_to(SERVICE_ROOT))
        for cls in _account_class_hits(text):
            if cls not in REVIEWED_CLASS_EXEMPTIONS:
                bad.append((name, f"class {cls}"))
        if "create_user" in lowered or "password_hash" in lowered:
            bad.append((name, "registration"))
    assert not bad, f"疑似自建账号库: {bad}"


def test_reviewed_exemptions_are_all_still_used() -> None:
    """豁免表不得积累死条目 —— 死条目会让它退化成"随便加就能过"。"""
    live: set[str] = set()
    for f in _py_files(PKG):
        live.update(_account_class_hits(_code_only(f.read_text(encoding="utf-8"))))
    assert set(REVIEWED_CLASS_EXEMPTIONS) <= live, (
        f"豁免表里有已不存在的类: {set(REVIEWED_CLASS_EXEMPTIONS) - live}"
    )
    for cls, reason in REVIEWED_CLASS_EXEMPTIONS.items():
        assert reason.strip(), f"{cls} 的豁免没有写理由"


def test_account_store_guard_still_catches_real_violations() -> None:
    """**元测试**：上一条守卫不得因为剥注释而变成永真。

    给它真的违规样本，要求它必须报出来。没有这一条，
    "守卫改成扫代码"就可能悄悄退化成"守卫永远通过" ——
    这正是本次改动引入的风险，因此必须被独立锁住。
    """
    for sample, expect in (
        ("class AccountRow(Base):\n    pass\n", "AccountRow"),
        ("class User(Base):\n    id = 1\n", "User"),
        ("class CredentialStore(object):\n    pass\n", "CredentialStore"),
        ("class UserAccountRow(BaseModel):\n    pass\n", "UserAccountRow"),
    ):
        hits = _account_class_hits(_code_only(sample))
        assert expect in hits, f"守卫漏掉了真正的账号表定义: {sample!r} -> {hits}"
        assert expect not in REVIEWED_CLASS_EXEMPTIONS, f"{expect} 不应在豁免表里"

    assert "password_hash" in _code_only("x = password_hash(pw)").lower()
    assert "create_user" in _code_only("def create_user():\n    pass").lower()

    # 而纯注释 / 字符串里的提及**不应**触发
    for benign in (
        '"""这里讨论 account 隔离，但不是 class Account(...) 定义。"""\n',
        'X = "class AccountRow(Base): pass"\n',
    ):
        code = _code_only(benign)
        assert not _account_class_hits(code), f"误报: {benign!r} -> {code!r}"
        assert "password_hash" not in code.lower(), f"误报: {benign!r}"


def test_port_and_prefix_frozen() -> None:
    from research_vnext.core.config import (
        DB_SCHEMA,
        EMBEDDING_DIMS,
        OBJECT_PREFIX,
        QUEUE_PREFIX,
        SERVICE_PORT,
    )

    assert SERVICE_PORT == 8300
    assert DB_SCHEMA == "rv8300"
    assert QUEUE_PREFIX == "strm:rv8300"
    assert OBJECT_PREFIX == "rvs/"
    assert EMBEDDING_DIMS == 768


def test_integration_db_is_real_postgres(pg_url: str) -> None:
    from tests.conftest import assert_real_postgres

    assert_real_postgres(pg_url)
    assert "sqlite" not in pg_url.lower()
    assert "memory" not in pg_url.lower()


def test_outbox_relay_lives_outside_session_service_and_never_fakes_success() -> None:
    """**2026-09-13（RV03）本用例被有意重写**，原断言是：

        assert "def publish" not in app_src
        assert "def relay" not in app_src

    原文意图是"RV02 只到 durable record，不得有假装已发布的逻辑"。RV03 的交付内容
    恰恰就是补上真实 relay，所以"不存在 relay"这条断言已经被**有意取代**。
    替换后的断言仍然守住**同一条安全性质**，而且更强：

    1. relay 必须真实存在，但**不得**挤进 `session_service`（写路径与投递路径分离，
       否则"发布"会在业务事务内被顺手调用，让"已发布"变成事务的一部分而非独立可观测事实）；
    2. `session_service` 里 `published_at=` 只能出现在**写入 NULL** 的地方
       —— 它必须永远不能把一个事件标成已发布；
    3. relay 必须用 `SKIP LOCKED`（并发安全）且必须在队列失败时**不回填**
       `published_at`（否则 Valkey 一挂就静默丢事件）。
    """
    models_src = (PKG / "core" / "db" / "models.py").read_text(encoding="utf-8")
    assert "published_at" in models_src

    app_src = (PKG / "core" / "application" / "session_service.py").read_text(encoding="utf-8")
    assert "published_at=None" in app_src, "写入路径必须显式把 published_at 置空"
    # 写路径**不得**标记发布，也不得自带 relay
    assert "published_at=now" not in app_src
    assert "published_at=_now()" not in app_src
    assert "def relay" not in app_src, "relay 不得与 session 写路径混在一起"
    assert "def publish" not in app_src, "session_service 不得直接发布"

    relay_path = PKG / "core" / "application" / "outbox_relay.py"
    assert relay_path.exists(), "RV03 必须存在真实 outbox relay"
    relay_src = relay_path.read_text(encoding="utf-8")
    assert "skip_locked=True" in relay_src, "relay 必须用 FOR UPDATE SKIP LOCKED 才能并发安全"
    assert "published_at = now" in relay_src, "relay 必须回填 published_at"
    # 队列失败时必须**不**回填 —— 否则 Valkey 一挂就静默丢事件
    assert "except QueueUnavailable" in relay_src
    assert "不回填 published_at" in relay_src
