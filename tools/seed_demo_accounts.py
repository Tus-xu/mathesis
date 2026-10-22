#!/usr/bin/env python3
"""Math Arena 三端演示账号与场景数据播种（幂等，可重复执行）。

本地:
  python tools/seed_demo_accounts.py --profile local

线上（需先设置 MATHESIS_DEPLOY_HOST_1 / MATHESIS_DEPLOY_HOST_2 环境变量）:
  python tools/seed_demo_accounts.py --profile host1
  python tools/seed_demo_accounts.py --profile host2

设计原则：
- 账号/班级/作业/课件/知识库/科研项目走真实 API；
- 学情快照、错题 FSRS、历史会话、科研 Agent 结果等 UI 投影数据用 SQL 补齐；
- 所有写入可幂等重跑（固定 client id / 标题查重 / ON CONFLICT）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2
import requests
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

try:
    import paramiko
except Exception:  # pragma: no cover
    paramiko = None

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "tmp" / "demo-seed"
PAPER_DIR = SEED_DIR / "papers"
KB_DIR = SEED_DIR / "kb"
PASSWORD = "MathArenaDemo2026!"

DEMO = {
    "student": {"phone": "13800001001", "nickname": "小林", "role": "student"},
    "teacher": {"phone": "13800001002", "nickname": "张老师", "role": "teacher"},
    "researcher": {"phone": "13800001003", "nickname": "林知远", "role": "researcher"},
    "classmates": [
        {"phone": "13800001004", "nickname": "小陈"},
        {"phone": "13800001005", "nickname": "小周"},
        {"phone": "13800001006", "nickname": "小许"},
        {"phone": "13800001007", "nickname": "小唐"},
    ],
}
CLASS_NAME = "高一(3)班 · 数学"

_DEPLOY_HOST_1 = os.environ.get("MATHESIS_DEPLOY_HOST_1", "http://host1.example.com")
_DEPLOY_HOST_2 = os.environ.get("MATHESIS_DEPLOY_HOST_2", "http://host2.example.com")
_DEPLOY_KEY_1 = os.environ.get("MATHESIS_DEPLOY_KEY_1", "~/.ssh/deploy_key_1")
_DEPLOY_KEY_2 = os.environ.get("MATHESIS_DEPLOY_KEY_2", "~/.ssh/deploy_key_2")

PROFILES: dict[str, dict[str, Any]] = {
    "local": {
        "base": "http://127.0.0.1:8000",
        "teacher_base": "http://127.0.0.1:8100/api/teacher-v3",
        "local_db": True,
        "math_db": dict(host="127.0.0.1", port=54330, user="teacher", password="teacher_dev_pw", dbname="math_arena"),
        "teacher_db": dict(host="127.0.0.1", port=54330, user="teacher", password="teacher_dev_pw", dbname="teacher_platform"),
        "research_db": dict(host="127.0.0.1", port=54329, user="postgres", password="postgres", dbname="research_vnext"),
    },
    "host1": {
        "base": _DEPLOY_HOST_1,
        "teacher_base": f"{_DEPLOY_HOST_1}/api/teacher-v3",
        "local_db": False,
        "ssh_host": _DEPLOY_HOST_1.replace("http://", ""),
        "ssh_user": "root",
        "ssh_key": os.path.expanduser(_DEPLOY_KEY_1),
        "math_container": "math-arena-postgres",
        "math_user": "postgres",
        "teacher_container": "teacher-postgres",
        "teacher_user": "teacher",
        "research_container": "math-arena-postgres",
    },
    "host2": {
        "base": _DEPLOY_HOST_2,
        "teacher_base": f"{_DEPLOY_HOST_2}/api/teacher-v3",
        "local_db": False,
        "ssh_host": _DEPLOY_HOST_2.replace("http://", ""),
        "ssh_user": "root",
        "ssh_key": os.path.expanduser(_DEPLOY_KEY_2),
        "math_container": "math-arena-postgres",
        "math_user": "postgres",
        "teacher_container": "teacher-postgres",
        "teacher_user": "teacher",
        "research_container": "math-arena-postgres",
    },
}


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

def lit(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def json_lit(value: Any) -> str:
    return lit(json.dumps(value, ensure_ascii=False)) + "::jsonb"


class Db:
    def __init__(self, prof: dict[str, Any]):
        self.prof = prof
        self.is_local = prof.get("local_db", False)
        self._ssh = None
        if not self.is_local:
            if paramiko is None:
                raise RuntimeError("paramiko is required for remote seeding")
            self._ssh = paramiko.SSHClient()
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._ssh.connect(
                prof["ssh_host"],
                username=prof.get("ssh_user", "root"),
                key_filename=prof["ssh_key"],
                timeout=20,
                look_for_keys=False,
            )

    def _local_conn(self, kind: str):
        dsn = self.prof["math_db"] if kind in ("math", "research") else self.prof["teacher_db"]
        if kind == "research":
            dsn = self.prof["research_db"]
        return psycopg2.connect(**dsn)

    def _remote_cmd(self, kind: str) -> str:
        if kind == "teacher":
            c, u, d = self.prof["teacher_container"], self.prof["teacher_user"], "teacher_platform"
        elif kind == "research":
            c, u, d = self.prof["research_container"], self.prof["math_user"], "research_vnext"
        else:
            c, u, d = self.prof["math_container"], self.prof["math_user"], "math_arena"
        env = "-e PGOPTIONS='-c search_path=rv8300' " if kind == "research" else ""
        return f"docker exec {env}-i {c} psql -U {u} -d {d} -v ON_ERROR_STOP=1 -At -F '\t'"

    def exec(self, kind: str, sql: str) -> list[list[str]]:
        if self.is_local:
            conn = self._local_conn(kind)
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SET search_path TO rv8300" if kind == "research" else "SET search_path TO public")
                        cur.execute(sql)
                        if cur.description:
                            return [[("" if v is None else str(v)) for v in row] for row in cur.fetchall()]
                        return []
            finally:
                conn.close()
        if self._ssh is None:
            raise RuntimeError("ssh not ready")
        cmd = self._remote_cmd(kind)
        stdin, stdout, stderr = self._ssh.exec_command(cmd, timeout=120)
        stdin.write(sql.encode("utf-8"))
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"psql failed ({kind}): {err[-1000:]}\nSQL: {sql[:400]}")
        rows: list[list[str]] = []
        for line in out.splitlines():
            if line == "":
                continue
            rows.append(line.split("\t"))
        return rows

    def one(self, kind: str, sql: str) -> str | None:
        rows = self.exec(kind, sql)
        return rows[0][0] if rows and rows[0] else None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class Api:
    def __init__(self, base: str, teacher_base: str):
        self.base = base.rstrip("/")
        self.teacher_base = teacher_base.rstrip("/")
        self.session = requests.Session()

    def _call(self, method: str, url: str, token: str | None = None, **kwargs) -> Any:
        headers = kwargs.pop("headers", {}) or {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = self.session.request(method, url, headers=headers, timeout=120, **kwargs)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:500]}")
        try:
            payload = r.json()
        except Exception:
            return r.text
        if isinstance(payload, dict) and "code" in payload:
            if payload.get("code") != 0:
                raise RuntimeError(f"{method} {url} -> code={payload.get('code')}: {payload.get('message')}")
            return payload.get("data")
        return payload

    def login(self, phone: str, role: str) -> tuple[str, dict[str, Any]]:
        ch = None
        for attempt in range(3):
            try:
                ch = self._call("POST", f"{self.base}/api/auth/challenges/sms", json={"phone": phone, "purpose": "login"})
                break
            except RuntimeError as e:
                if attempt < 2 and ("429" in str(e) or "频繁" in str(e) or "rate" in str(e).lower()):
                    log(f"login rate limited for {phone}, waiting 62s")
                    time.sleep(62)
                    continue
                raise
        if not ch:
            raise RuntimeError(f"login challenge failed for {phone}")
        code = (ch or {}).get("demo_code") or "123456"
        data = self._call(
            "POST",
            f"{self.base}/api/auth/login/sms",
            json={"phone": phone, "challenge_id": ch["challenge_id"], "code": code, "preferred_role": role, "remember": False},
        )
        token = data["access_token"]
        user = data.get("user") or {}
        return token, user

    def set_password(self, token: str) -> None:
        self._call("POST", f"{self.base}/api/auth/password/set", token=token, json={"password": PASSWORD})

    def get(self, path: str, token: str, **kwargs):
        return self._call("GET", f"{self.base}{path}", token=token, **kwargs)

    def post(self, path: str, token: str, **kwargs):
        return self._call("POST", f"{self.base}{path}", token=token, **kwargs)

    def patch(self, path: str, token: str, **kwargs):
        return self._call("PATCH", f"{self.base}{path}", token=token, **kwargs)

    def teacher(self, method: str, path: str, token: str, **kwargs):
        return self._call(method, f"{self.teacher_base}{path}", token=token, **kwargs)

    def research(self, method: str, path: str, token: str, **kwargs):
        return self._call(method, f"{self.base}/api/research-vnext{path}", token=token, **kwargs)


# ---------------------------------------------------------------------------
# Accounts / class
# ---------------------------------------------------------------------------

def ensure_user(api: Api, db: Db, phone: str, role: str, nickname: str, org: str = "") -> dict[str, Any]:
    token, user = api.login(phone, role)
    uid = user["id"]
    try:
        api.set_password(token)
    except Exception as e:
        log(f"password set skipped for {phone}: {str(e)[:120]}")
    db.exec(
        "math",
        f"""UPDATE users SET nickname={lit(nickname)}, onboarding_status='completed' WHERE id={lit(uid)}::uuid;
        UPDATE role_bindings SET org_name={lit(org)}, status='approved', approved_at=now(), verified=true
          WHERE user_id={lit(uid)}::uuid AND role={lit(role)} AND deleted_at IS NULL;
        INSERT INTO user_profiles (user_id, grade, level, weak_points, preferences)
          VALUES ({lit(uid)}::uuid, {'高一' if role == 'student' else ''!r}, 'intermediate', '[]'::jsonb, '{{}}'::jsonb)
          ON CONFLICT (user_id) DO UPDATE SET grade=EXCLUDED.grade, level=EXCLUDED.level;
        """,
    )
    if role == "student":
        db.exec(
            "math",
            f"""INSERT INTO student_profiles
              (user_id, school_stage, grade, tags, weak_point_rank, learning_style, current_stage, profile_card, computed_at)
            VALUES ({lit(uid)}::uuid, '高中', '高一', {json_lit(['函数基础扎实', '正在补导数与立体几何', '适合短时高频练习'])},
              '[]'::jsonb, 'practice', '导数专题突破', '小林最近在导数与函数单调性上投入较多，错题集中在分类讨论与端点判断。', now())
            ON CONFLICT (user_id) DO UPDATE SET tags=EXCLUDED.tags, profile_card=EXCLUDED.profile_card, computed_at=now();""",
        )
    return {"token": token, "id": uid, "phone": phone, "nickname": nickname, "role": role}


def ensure_class(api: Api, db: Db, teacher: dict[str, Any], students: list[dict[str, Any]]) -> dict[str, Any]:
    rows = db.exec(
        "math",
        f"SELECT id, name, invite_code FROM classes WHERE owner_id={lit(teacher['id'])}::uuid AND name={lit(CLASS_NAME)} AND deleted_at IS NULL LIMIT 1;",
    )
    if rows:
        class_id, name, invite = rows[0][0], rows[0][1], rows[0][2]
    else:
        data = api.post("/api/classes", teacher["token"], json={"name": CLASS_NAME, "grade": "高一"})
        class_id, name, invite = data["id"], data["name"], data["inviteCode"]
        log(f"class created: {class_id}")
    for st in students:
        try:
            api.post("/api/classes/join", st["token"], json={"invite_code": invite, "nickname": st["nickname"]})
        except Exception as e:
            if "已加入" not in str(e) and "唯一" not in str(e):
                log(f"join skipped for {st['nickname']}: {str(e)[:100]}")
        api.patch(f"/api/classes/{class_id}/members/{st['id']}", teacher["token"], json={"confirmed": True})
        db.exec(
            "math",
            f"""UPDATE class_members SET confirmed=true, nickname_in_class={lit(st['nickname'])}
                WHERE class_id={lit(class_id)}::uuid AND user_id={lit(st['id'])}::uuid;""",
        )
    return {"id": class_id, "name": name, "invite": invite}


# ---------------------------------------------------------------------------
# PDF / KB content
# ---------------------------------------------------------------------------

PAPERS = [
    ("Verifiable Chain-of-Thought for Geometric Reasoning", "Lin Zhiyuan, Chen Yu, Zhou Ming"),
    ("Benchmarking LLM Feedback in High School Mathematics", "Xu Ran, Tang Wei, Lin Zhiyuan"),
    ("Evidence-Anchored LaTeX Writing for Research Teams", "Lin Zhiyuan, Xu Ran"),
    ("Multimodal Error Diagnosis with BKT and FSRS", "Chen Yu, Zhou Ming, Lu Yao"),
    ("Retrieval-Augmented Problem Generation for Calculus", "Zhou Ming, Lin Zhiyuan"),
    ("Human-in-the-Loop Grading for Mathematics Homework", "Tang Wei, Chen Yu"),
]


def ensure_pdfs() -> list[Path]:
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PaperTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21, spaceAfter=8)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=14, spaceAfter=7)
    head = ParagraphStyle("Head", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=5)
    for idx, (title, authors) in enumerate(PAPERS):
        path = PAPER_DIR / f"{title.replace(' ', '_').replace('/', '-')}.pdf"
        paths.append(path)
        if path.exists() and path.stat().st_size > 1000:
            continue
        story = [
            Paragraph(title, title_style),
            Paragraph(authors, body),
            Spacer(1, 0.25 * cm),
            Paragraph("Abstract", head),
            Paragraph(
                "We study how large language models can produce mathematical arguments whose intermediate claims "
                "are individually checkable and traceable to evidence. Our benchmark combines symbolic verification, "
                "retrieval over source documents, and expert review. Results suggest that explicit evidence anchors "
                "reduce unsupported claims without sacrificing coverage.",
                body,
            ),
            Paragraph("1. Introduction", head),
            Paragraph(
                "Mathematical reasoning is often evaluated only by its final answer. This paper argues that the "
                "intermediate chain must also be auditable. We define a verifiable claim as a statement with a local "
                "justification, a declared set of assumptions, and an evidence reference when external knowledge is used.",
                body,
            ),
            Paragraph("2. Method", head),
            Paragraph(
                "Given a problem P and a candidate proof s1,...,sn, each step is checked by a deterministic symbolic "
                "tool. If a step cannot be verified, it is marked as requiring additional conditions rather than accepted. "
                "The evidence layer links each external claim to a source span and a retrieval score.",
                body,
            ),
            Paragraph("3. Findings", head),
            Paragraph(
                "Across 240 annotated solutions, evidence anchoring lowered the rate of unsupported intermediate "
                "claims from 31% to 12%. Human reviewers reported that page-level evidence chips made verification "
                "substantially faster. Remaining errors cluster around implicit domain assumptions.",
                body,
            ),
            Paragraph("4. Discussion", head),
            Paragraph(
                "Verifiability is not the same as correctness. A proof can be formally checkable yet rely on an "
                "undeclared assumption. A useful research workspace should therefore keep assumptions, citations, "
                "and source locations attached to the same project object.",
                body,
            ),
        ]
        doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=2 * cm, leftMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm)
        doc.build(story)
    return paths


def ensure_kb_files() -> dict[str, Path]:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "导数与函数单调性_课堂笔记.md": ("笔记", """# 导数与函数单调性

## 核心结论
- 若在区间 I 上 $f'(x)>0$，则 $f(x)$ 在 I 上单调递增。
- 若在区间 I 上 $f'(x)<0$，则 $f(x)$ 在 I 上单调递减。
- 极值点必须满足 $f'(x)=0$ 或导数不存在，并且两侧导数变号。

## 易错点
1. 只解 $f'(x)=0$ 就判断极值，忘记检查两侧符号。
2. 在闭区间求最值时，忘记比较端点值。
3. 含参数函数要按参数分类讨论。

## 例题
已知 $f(x)=x^3-3x$，求单调区间与极值。
"""),
        "三角函数恒等变换_错题整理.md": ("笔记", """# 三角函数恒等变换

## 常用公式
$$\\sin^2 x+\\cos^2 x=1$$
$$\\sin 2x=2\\sin x\\cos x$$
$$\\cos 2x=\\cos^2 x-\\sin^2 x$$

## 错题摘录
若 $\\sin\\alpha+\\cos\\alpha=\\frac15$，则 $\\sin 2\\alpha=-\\frac{24}{25}$。
错因：平方后忘记用 $\\sin^2\\alpha+\\cos^2\\alpha=1$ 化简。
"""),
        "空间几何体外接球模型.md": ("教辅", """# 空间几何体外接球模型

## 正方体
棱长为 $a$ 的正方体，外接球半径 $R=\\frac{\\sqrt3}{2}a$，表面积 $S=4\\pi R^2=3\\pi a^2$。

## 长方体
长宽高分别为 $a,b,h$ 时，$R=\\frac12\\sqrt{a^2+b^2+h^2}$。

## 解题步骤
1. 找几何体的对称中心或三条互相垂直的棱。
2. 建立坐标系，求外接球球心。
3. 用半径公式求表面积或体积。
"""),
        "2024新高考I卷_导数真题选编.md": ("试卷", """# 2024 新高考 I 卷 · 导数真题选编

## 题目
已知函数 $f(x)=x^3-3x$。

1. 求 $f(x)$ 的单调区间；
2. 求 $f(x)$ 在 $[-2,2]$ 上的最值；
3. 若方程 $f(x)=a$ 有三个不同实根，求 $a$ 的取值范围。

## 命题意图
考查导数与函数单调性、极值、最值以及数形结合思想。
"""),
    }
    paths = {}
    for name, (_, content) in files.items():
        p = KB_DIR / name
        p.write_text(content, encoding="utf-8")
        paths[name] = p
    return paths


def upload_kb(api: Api, db: Db, student: dict[str, Any]) -> None:
    files = ensure_kb_files()
    for name, (category, _) in {
        "导数与函数单调性_课堂笔记.md": ("笔记", ""),
        "三角函数恒等变换_错题整理.md": ("笔记", ""),
        "空间几何体外接球模型.md": ("教辅", ""),
        "2024新高考I卷_导数真题选编.md": ("试卷", ""),
    }.items():
        existing = db.one(
            "math",
            f"SELECT id FROM knowledge_docs WHERE uploader_id={lit(student['id'])}::uuid AND title={lit(name)} AND deleted_at IS NULL LIMIT 1;",
        )
        if existing:
            continue
        path = files[name]
        with path.open("rb") as fh:
            r = api.session.post(
                f"{api.base}/api/v1/kb/upload",
                headers={"Authorization": f"Bearer {student['token']}"},
                data={"kb_name": "default", "category": category, "user_id": student["id"]},
                files={"file": (name, fh, "text/markdown")},
                timeout=120,
            )
        if r.status_code >= 400 or (r.json().get("code") not in (0, None)):
            log(f"KB upload failed {name}: {r.status_code} {r.text[:200]}")
        else:
            log(f"KB uploaded: {name}")
    # 等后台解析（最多 30 秒）
    for _ in range(15):
        pending = db.one(
            "math",
            f"SELECT count(*) FROM knowledge_docs WHERE uploader_id={lit(student['id'])}::uuid AND deleted_at IS NULL AND status IN ('pending','parsing','chunking','embedding');",
        )
        if (pending or "0") == "0":
            break
        time.sleep(2)


# ---------------------------------------------------------------------------
# Student data (mastery/errors/conversations)
# ---------------------------------------------------------------------------

def kp_rows(db: Db) -> list[tuple[str, str]]:
    rows = db.exec(
        "math",
        """SELECT kp.code, kp.name, count(q.id) AS n
           FROM knowledge_points kp
           LEFT JOIN question_bank q ON kp.code = ANY(q.kp_codes) AND q.deleted_at IS NULL AND q.scope='student'
           WHERE kp.code IS NOT NULL AND kp.name IS NOT NULL
           GROUP BY kp.code, kp.name
           ORDER BY n DESC, kp.code
           LIMIT 12;""",
    )
    return [(r[0], r[1]) for r in rows]


def seed_student_learning(db: Db, uid: str) -> list[tuple[str, str]]:
    kps = kp_rows(db)
    if not kps:
        raise RuntimeError("knowledge_points is empty; cannot seed learning data")
    # 前三项薄弱、后两项已掌握：首页/报告既有“待补”也有真实掌握进度
    values = [0.34, 0.42, 0.55, 0.72, 0.81, 0.86, 0.63, 0.66, 0.74, 0.79, 0.83, 0.88]
    for i, (code, _name) in enumerate(kps):
        mastery = values[i % len(values)]
        db.exec(
            "math",
            f"""INSERT INTO mastery_records (user_id, kp_id, mastery, practice_count, correct_count, hint_count, last_practiced_at)
                SELECT {lit(uid)}::uuid, id, {mastery}, {12 + i * 2}, {max(1, int((12 + i * 2) * mastery))}, {i % 5}, now() - interval '{i % 4} day'
                FROM knowledge_points WHERE code={lit(code)}
                ON CONFLICT (user_id, kp_id) DO UPDATE SET mastery=EXCLUDED.mastery,
                  practice_count=EXCLUDED.practice_count, correct_count=EXCLUDED.correct_count,
                  hint_count=EXCLUDED.hint_count, last_practiced_at=EXCLUDED.last_practiced_at;""",
        )
    # 30 天趋势：一次批量 upsert（远程环境避免 360 次 psql 调用）
    rows: list[str] = []
    today = datetime.now(timezone.utc).date()
    for i, (code, _name) in enumerate(kps):
        mastery = values[i % len(values)]
        for offset in range(0, 30):
            val = round(max(0.18, mastery - offset * 0.009 + ((offset * 7 + i * 3) % 5) * 0.002), 4)
            day = (today - timedelta(days=offset)).isoformat()
            rows.append(f"(gen_random_uuid(), {lit(uid)}::uuid, {lit(code)}, {lit(day)}::date, {val})")
    db.exec(
        "math",
        "INSERT INTO mastery_snapshots (id, user_id, kp_code, date, mastery) VALUES "
        + ",".join(rows)
        + " ON CONFLICT (user_id, kp_code, date) DO UPDATE SET mastery=EXCLUDED.mastery;",
    )
    # 学生画像薄弱点
    weak = [{"kp_code": c, "kp_name": n, "mastery": values[i] if i < len(values) else 0.4}
            for i, (c, n) in enumerate(kps[:4])]
    db.exec(
        "math",
        f"""UPDATE user_profiles SET weak_points={json_lit(weak)} WHERE user_id={lit(uid)}::uuid;
            UPDATE student_profiles SET weak_point_rank={json_lit(weak)}, current_stage='导数专题突破', computed_at=now()
              WHERE user_id={lit(uid)}::uuid;""",
    )
    return kps


def seed_errors(db: Db, uid: str, kps: list[tuple[str, str]]) -> None:
    items = [
        ("已知函数 $f(x)=x^3-3x+1$，求 $f(x)$ 的极值，并判断 $f(x)=0$ 的实根个数。", "极大值 $f(-1)=3$，极小值 $f(1)=-1$；有三个实根。", "concept", "极值点与端点混淆，忘记检查两侧导数变号。", 3.2, 1, 2, -1),
        ("若 $\\sin\\alpha+\\cos\\alpha=\\frac15$，且 $0<\\alpha<\\pi$，求 $\\tan\\alpha$。", "$-\\frac43$", "formula", "平方后没有结合象限判断符号。", 2.6, 0, 1, -2),
        ("正方体棱长为 $2$，其外接球的体积为（ ）\n\n| 选项 | 结果 |\n| :-: | :-: |\n| A | $4\\sqrt3\\pi$ |\n| B | $8\\sqrt3\\pi$ |\n| C | $4\\pi$ |\n| D | $12\\pi$ |", "A", "calculation", "外接球半径公式记成棱长一半。", 4.1, 2, 1, -3),
        ("函数 $y=2x^2-e^{|x|}$ 在 $[-2,2]$ 上的图象大致为（ ）", "A", "logic", "没有先判断奇偶性，导致排除困难。", 6.4, 1, 1, -5),
        ("已知抛物线 $y=x^2-4x+3$ 的顶点为 $C$，与 $x$ 轴交于 $A,B$，求 $\\triangle ABC$ 的面积。", "1", "reading", "把顶点横坐标看成了 4。", 8.2, 2, 1, -8),
        ("讨论函数 $f(x)=\\ln x-\\frac{a}{x}$ 在 $(0,+\\infty)$ 上的单调性。", "按 $a\\le0$ 与 $a>0$ 分类讨论。", "logic", "分类讨论遗漏 $a=0$。", 5.5, 0, 1, 2),
        ("已知 $\\vec a=(1,2)$，$\\vec b=(x,1)$，若 $\\vec a\\perp\\vec b$，求 $x$。", "$-2$", "calculation", "数量积公式符号写反。", 12.0, 1, 1, 4),
    ]
    for idx, (q, ans, et, note, stability, reviews, wrongs, due_off) in enumerate(items):
        code = kps[idx % len(kps)][0]
        when = "now() - interval '" + str(abs(due_off) + 6) + " day'"
        next_at = f"now() - interval '{abs(due_off)} day'" if due_off < 0 else f"now() + interval '{due_off} day'"
        db.exec(
            "math",
            f"""INSERT INTO error_records
              (id, user_id, question_text, answer_text, source_channel, error_type, kp_code, next_review_at,
               review_count, wrong_count, note, origin, fsrs_stability, fsrs_difficulty, created_at, updated_at)
            VALUES (gen_random_uuid(), {lit(uid)}::uuid, {lit(q)}, {lit(ans)}, 'auto_judge', {lit(et)}, {lit(code)},
              {next_at}, {reviews}, {wrongs}, {lit(note)}, {lit(['self_test','chat_quiz','manual_photo'][idx % 3])},
              {stability}, 5.5, {when}, {when})
            ON CONFLICT DO NOTHING;""",
        )
    db.exec(
        "math",
        f"""INSERT INTO streaks (user_id, current_streak, longest_streak, last_active_date, updated_at)
            VALUES ({lit(uid)}::uuid, 12, 18, current_date, now())
            ON CONFLICT (user_id) DO UPDATE SET current_streak=EXCLUDED.current_streak,
              longest_streak=EXCLUDED.longest_streak, last_active_date=current_date, updated_at=now();""",
    )
    # 周作答事实（作业之外再补 2 次练习，供首页“本周 N 题/正确率”）
    for s in range(2):
        client_id = f"demo-practice-{s}"
        if db.one(
            "math",
            f"SELECT id FROM submissions WHERE user_id={lit(uid)}::uuid AND client_submit_id={lit(client_id)} AND deleted_at IS NULL LIMIT 1;",
        ):
            continue
        sid = str(uuid.uuid4())
        created = f"now() - interval '{s + 1} day'"
        db.exec(
            "math",
            f"""INSERT INTO submissions (id, user_id, client_submit_id, status, total_score, created_at, updated_at)
                VALUES ({lit(sid)}::uuid, {lit(uid)}::uuid, {lit(client_id)}, 'graded', 30, {created}, {created});""",
        )
        for j in range(5):
            db.exec(
                "math",
                f"""INSERT INTO submission_items
                  (id, submission_id, item_no, q_type, answer_text, verdict, score, created_at, updated_at)
                VALUES (gen_random_uuid(), {lit(sid)}::uuid, {j+1}, 'choice', {lit('B')},
                  {lit('correct' if (j + s) % 2 == 0 else 'wrong')}, {10 if (j + s) % 2 == 0 else 0}, {created}, {created});""",
            )
    # 学习事件（管家时间线）
    for e in ("practice_completed", "error_reviewed", "chat_started"):
        db.exec(
            "math",
            f"""INSERT INTO events (user_id, event, props, created_at)
                VALUES ({lit(uid)}::uuid, {lit(e)}, '{{}}'::jsonb, now() - interval '{e == 'chat_started' and 1 or 2} day');""",
        )


def seed_conversations(db: Db, uid: str) -> None:
    convs = [
        {
            "title": "导数的几何意义怎么理解", "pinned": True, "minutes_ago": 8,
            "messages": [
                ("user", "导数的几何意义怎么理解？"),
                ("assistant", "导数 $f'(x_0)$ 的几何意义，是曲线 $y=f(x)$ 在点 $(x_0,f(x_0))$ 处切线的斜率。$f'(x_0)>0$ 时切线向右上，函数在附近递增；$f'(x_0)<0$ 时切线向右下，函数递减。"),
                ("user", "那为什么 $f'(x)=0$ 不一定是极值点？比如 $f(x)=x^3$，在 $x=0$ 处导数为 0，但函数一直单调递增。"),
                ("assistant", "因为 $f'(x_0)=0$ 只说明切线水平，不保证两侧导数变号。$f(x)=x^3$ 在 $x=0$ 两侧导数都为正，所以没有极值。判断极值还要看第一充分条件：导数在 $x_0$ 两侧是否由正变负（极大）或由负变正（极小）。"),
            ],
        },
        {
            "title": "分析我的薄弱点", "pinned": False, "minutes_ago": 26,
            "messages": [
                ("user", "帮我分析一下我现在的薄弱点。"),
                ("assistant", "你当前最薄弱的是「复数的乘、除运算」（掌握度约 34%）和「椭圆的简单几何性质」（约 42%）。导数的几何意义也需要再巩固，错题主要集中在极值点判断和单调区间书写。"),
                ("user", "那我应该先练什么？"),
                ("assistant", "建议先做 3 道导数与单调性的变式题，巩固“求导 → 找驻点 → 判断符号”的流程；再回到错题本复习两道到期错题。需要的话我可以直接带你做第一道。"),
            ],
        },
        {
            "title": "三角函数化简总出错", "pinned": False, "minutes_ago": 42,
            "messages": [
                ("user", "三角函数化简总出错，有没有通用的思路？"),
                ("assistant", "按“角 → 函数名 → 次数”的顺序看：先统一角，再统一函数名，最后降次。遇到 $\\sin\\alpha+\\cos\\alpha$ 可以先平方，得到 $1+\\sin2\\alpha$。"),
                ("user", "比如 $\\sin\\alpha+\\cos\\alpha=\\frac15$ 时，怎么求 $\\sin2\\alpha$？"),
                ("assistant", "两边平方：$1+\\sin2\\alpha=\\frac1{25}$，所以 $\\sin2\\alpha=-\\frac{24}{25}$。注意先判断角所在的象限，再决定符号。"),
            ],
        },
        {
            "title": "正方体外接球半径", "pinned": False, "minutes_ago": 58,
            "messages": [
                ("user", "棱长为 $a$ 的正方体，外接球半径怎么求？"),
                ("assistant", "正方体的体对角线是 $\\sqrt3a$，外接球直径就是体对角线，所以半径 $R=\\frac{\\sqrt3}{2}a$。"),
                ("user", "棱长为 2 时，外接球表面积是多少？"),
                ("assistant", "$R=\\sqrt3$，表面积 $S=4\\pi R^2=4\\pi\\cdot3=12\\pi$。"),
            ],
        },
        {
            "title": "本周学习复盘", "pinned": False, "minutes_ago": 1440,
            "messages": [
                ("user", "帮我复盘一下本周的学习。"),
                ("assistant", "本周你完成了 3 次练习和 1 份作业，函数单调性正确率提升明显；已经连续学习 12 天，本周完成 15 道题。"),
                ("user", "下周怎么安排？"),
                ("assistant", "建议把「导数」作为主线，每天 15 分钟：1 道概念题 + 1 道变式 + 1 道错题复习；周末再做一次整卷模拟。"),
            ],
        },
    ]
    for spec in convs:
        title = spec["title"]
        pinned = spec["pinned"]
        minutes_ago = spec["minutes_ago"]
        msgs = spec["messages"]
        existing = db.one(
            "math",
            f"SELECT id FROM conversations WHERE user_id={lit(uid)}::uuid AND title={lit(title)} AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1;",
        )
        if existing:
            cid = existing
            real_count = db.one(
                "math",
                f"SELECT count(*) FROM messages WHERE conversation_id={lit(cid)}::uuid AND (client_msg_id IS NULL OR client_msg_id NOT LIKE 'demo-%');",
            )
            if int(real_count or 0) > 0:
                log(f"conversation kept (real messages): {title}")
                continue
            db.exec("math", f"DELETE FROM messages WHERE conversation_id={lit(cid)}::uuid;")
            db.exec(
                "math",
                f"""UPDATE conversations SET summary='', message_count={len(msgs)}, pinned={lit(pinned)},
                      created_at=now() - interval '{minutes_ago} minutes',
                      updated_at=now() - interval '{minutes_ago} minutes' + interval '{len(msgs) * 2} minutes'
                    WHERE id={lit(cid)}::uuid;""",
            )
        else:
            cid = str(uuid.uuid4())
            db.exec(
                "math",
                f"""INSERT INTO conversations (id, user_id, active_role, title, summary, message_count, pinned, created_at, updated_at)
                    VALUES ({lit(cid)}::uuid, {lit(uid)}::uuid, 'student', {lit(title)}, '', {len(msgs)}, {lit(pinned)},
                      now() - interval '{minutes_ago} minutes',
                      now() - interval '{minutes_ago} minutes' + interval '{len(msgs) * 2} minutes');""",
            )
        for seq, (role, text) in enumerate(msgs, start=1):
            mid = str(uuid.uuid4())
            env = {"msg_id": mid, "role": role, "blocks": [{"type": "markdown", "content": text}], "meta": {"ai_generated": role == "assistant"}}
            db.exec(
                "math",
                f"""INSERT INTO messages
                  (id, conversation_id, client_msg_id, role, content, envelope, skill_id, route_info, created_at, updated_at)
                VALUES ({lit(mid)}::uuid, {lit(cid)}::uuid, {lit(f'demo-{cid[:8]}-{seq}')}, {lit(role)}, {lit(text)},
                  {json_lit(env)}, {'NULL' if role == 'user' else lit('chat')}, '{{}}'::jsonb,
                  now() - interval '{minutes_ago} minutes' + interval '{seq * 2} minutes',
                  now() - interval '{minutes_ago} minutes' + interval '{seq * 2} minutes');""",
            )


# ---------------------------------------------------------------------------
# Assignment / courseware
# ---------------------------------------------------------------------------

ASSIGNMENT_QUESTIONS = [
    {"item_no": 1, "q_type": "choice", "question_text": "已知函数 $f(x)=x^3-3x$，则 $f(x)$ 的单调递减区间是（  ）",
     "options": {"A": "$(-\\infty,-1)$", "B": "$(-1,1)$", "C": "$(1,+\\infty)$", "D": "$(-\\infty,+\\infty)$"},
     "answer": "B", "analysis": "由 $f'(x)=3x^2-3<0$ 得 $-1<x<1$。", "kp_code": None, "difficulty": "medium"},
    {"item_no": 2, "q_type": "choice", "question_text": "若 $\\sin\\alpha+\\cos\\alpha=\\frac15$，且 $0<\\alpha<\\pi$，则 $\\tan\\alpha=$（  ）",
     "options": {"A": "$-\\frac43$", "B": "$-\\frac34$", "C": "$\\frac34$", "D": "$\\frac43$"},
     "answer": "A", "analysis": "平方后得 $\\sin2\\alpha=-\\frac{24}{25}$，结合象限可得 $\\tan\\alpha=-\\frac43$。", "kp_code": None, "difficulty": "hard"},
    {"item_no": 3, "q_type": "choice", "question_text": "棱长为 $2$ 的正方体的外接球表面积为（  ）",
     "options": {"A": "$4\\pi$", "B": "$8\\pi$", "C": "$12\\pi$", "D": "$16\\pi$"},
     "answer": "C", "analysis": "$R=\\sqrt3$，$S=4\\pi R^2=12\\pi$。", "kp_code": None, "difficulty": "medium"},
    {"item_no": 4, "q_type": "blank", "question_text": "抛物线 $y=x^2-4x+3$ 的顶点坐标是____。",
     "options": None, "answer": "(2,-1)", "analysis": "配方得 $y=(x-2)^2-1$。", "kp_code": None, "difficulty": "easy"},
    {"item_no": 5, "q_type": "choice", "question_text": "若函数 $f(x)=\\ln x-\\frac{a}{x}$ 在 $(0,+\\infty)$ 上单调递增，则 $a$ 的取值范围是（  ）",
     "options": {"A": "$a\\le0$", "B": "$a<0$", "C": "$a\\ge0$", "D": "$a>0$"},
     "answer": "C", "analysis": "$f'(x)=\\frac{x+a}{x^2}\\ge0$ 对任意 $x>0$ 恒成立，故 $a\\ge0$。", "kp_code": None, "difficulty": "hard"},
]


def pick_kp(kps: list[tuple[str, str]], idx: int) -> str | None:
    return kps[idx % len(kps)][0] if kps else None


def ensure_figure_payload() -> dict[str, Any] | None:
    """用真实确定性渲染器生成一张双帧函数图（生产/本地同一份 SVG 帧）。"""
    out = SEED_DIR / "figure-monotonic.json"
    if out.exists():
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            pass
    py = ROOT / "services" / "api" / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        log("figure renderer venv missing; skip figure demo")
        return None
    code = r'''
import json
from app.services.figure_renderer import render_figure_frames
fig = {
  "version": 1, "type": "function",
  "params": {
    "curves": [{"expr": "x**3-3*x", "label": "y=x³-3x", "domain": [-2.5, 2.5]}],
    "x_range": [-2.5, 2.5], "y_range": [-4, 4],
    "points": [{"x": -1, "y": 2, "label": "(-1,2)"}, {"x": 1, "y": -2, "label": "(1,-2)"}],
    "asymptotes": {"x": [], "y": []}
  }
}
print(json.dumps(render_figure_frames(fig, step_no=1, caption="观察 x=-1 与 x=1 两侧的导数符号变化"), ensure_ascii=False))
'''
    import subprocess
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [str(py), "-c", code],
        cwd=str(ROOT / "services" / "api"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if proc.returncode != 0:
        log(f"figure render failed: {proc.stderr[-300:]}")
        return None
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    except Exception as e:
        log(f"figure payload parse failed: {e}")
        return None


def seed_showcase_conversations(db: Db, uid: str, figure: dict[str, Any] | None) -> None:
    """引导式解题 / 举一反三 / 可视化讲解 / 错题本录入 四类可交互展示。"""
    def conv(title: str, pinned: bool, days: int, msgs: list[dict[str, Any]]) -> None:
        cid = db.one(
            "math",
            f"SELECT id FROM conversations WHERE user_id={lit(uid)}::uuid AND title={lit(title)} AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1;",
        )
        if cid:
            db.exec(
                "math",
                f"""UPDATE conversations SET pinned=true,
                      updated_at=now() - interval '{days} day' + interval '{len(msgs) * 3} minute'
                    WHERE id={lit(cid)}::uuid;""",
            )
            return
        cid = str(uuid.uuid4())
        when = f"now() - interval '{days} day'"
        db.exec(
            "math",
            f"""INSERT INTO conversations (id, user_id, active_role, title, summary, message_count, pinned, created_at, updated_at)
                VALUES ({lit(cid)}::uuid, {lit(uid)}::uuid, 'student', {lit(title)}, '', {len(msgs)}, true, {when}, {when});""",
        )
        for seq, msg in enumerate(msgs, start=1):
            mid = str(uuid.uuid4())
            role = msg["role"]
            blocks = msg.get("blocks") or [{"type": "markdown", "content": msg.get("text", "")}]
            env = {"msg_id": mid, "role": role, "blocks": blocks, "meta": {"skill": msg.get("skill", "chat"), "ai_generated": role == "assistant"}}
            db.exec(
                "math",
                f"""INSERT INTO messages
                  (id, conversation_id, client_msg_id, role, content, envelope, skill_id, route_info, created_at, updated_at)
                VALUES ({lit(mid)}::uuid, {lit(cid)}::uuid, {lit(f'demo-show-{cid[:8]}-{seq}')}, {lit(role)},
                  {lit(msg.get('text', ''))}, {json_lit(env)}, {lit(msg.get('skill', 'chat'))}, '{{}}'::jsonb,
                  now() - interval '{days} day' + interval '{seq * 3} minute',
                  now() - interval '{days} day' + interval '{seq * 3} minute');""",
            )
        db.exec(
            "math",
            f"UPDATE conversations SET updated_at=now() - interval '{days} day' + interval '{len(msgs) * 3} minute' WHERE id={lit(cid)}::uuid;",
        )

    guide_blocks = [
        {"type": "markdown", "content": "我们先不急着算。你已经知道要求极值，第一步应该先关注什么？"},
        {"type": "card", "data": {"type": "socratic_start", "steps_count": 4, "difficulty": "medium"}},
        {"type": "card", "data": {"type": "socratic_step_choice", "step": 1,
            "options": ["先求导数，找驻点", "先随便代入几个 x 看函数值", "先画一个大概的图象"]}},
    ]
    guide_done_blocks = [
        {"type": "markdown", "content": "很好。$f'(x)=3x^2-3=3(x-1)(x+1)$。接下来看导数在驻点两侧是否变号。"},
        *([{"type": "figure", **figure}] if figure else []),
        {"type": "card", "data": {"type": "socratic_complete", "outcome": "guided", "difficulty": "medium", "verified": True,
            "final_answer": "极大值 $f(-1)=2$，极小值 $f(1)=-2$；递增区间 $(-\\infty,-1)$ 与 $(1,+\\infty)$，递减区间 $(-1,1)$。",
            "steps": [
                {"assertion": "求导：$f'(x)=3x^2-3=3(x-1)(x+1)$", "reason": "极值候选点来自 $f'(x)=0$。"},
                {"assertion": "在 $x=-1$ 两侧导数由正变负，故为极大值点", "reason": "第一充分条件：导数变号。"},
                {"assertion": "在 $x=1$ 两侧导数由负变正，故为极小值点", "reason": "第一充分条件：导数变号。"},
            ],
            "hint_stats": {"total": 1}, "duration_s": 260},
        },
    ]
    conv("引导式解题 · 三次函数极值", True, 0, [
        {"role": "user", "text": "已知 $f(x)=x^3-3x$，求极值。我想自己试试，但先给我一点方向。"},
        {"role": "assistant", "blocks": guide_blocks, "skill": "socratic_solver"},
        {"role": "user", "text": "先求导数，令 $f'(x)=0$ 找驻点。"},
        {"role": "assistant", "blocks": guide_done_blocks, "skill": "socratic_solver"},
    ])

    variant_items = [
        {"item_no": 1, "q_type": "choice", "question_text": "已知函数 $f(x)=2x^3-3x^2-12x+1$，则 $f(x)$ 的极小值点为（  ）",
         "options": ["A. $x=-1$", "B. $x=1$", "C. $x=2$", "D. $x=-2$"], "answer": "C",
         "answer_analysis": "$f'(x)=6x^2-6x-12=6(x-2)(x+1)$，在 $x=2$ 两侧由负变正，故为极小值点。",
         "difficulty": "easy", "variant_note": "换函数系数，保持导数变号结构", "source": "user_variant", "verified": True},
        {"item_no": 2, "q_type": "choice", "question_text": "若函数 $f(x)=x^3+ax^2+3x$ 在 $\\mathbb R$ 上单调递增，则 $a$ 的取值范围是（  ）",
         "options": ["A. $[-3,3]$", "B. $(-3,3)$", "C. $[-9,9]$", "D. $(-9,9)$"], "answer": "A",
         "answer_analysis": "$f'(x)=3x^2+2ax+3\\ge0$ 恒成立，判别式 $4a^2-36\\le0$，得 $-3\\le a\\le3$。",
         "difficulty": "medium", "variant_note": "条件改为恒单调，加入参数范围", "source": "user_variant", "verified": True},
        {"item_no": 3, "q_type": "choice", "question_text": "已知 $f(x)=x^3-3x$，若方程 $f(x)=a$ 有三个不同实根，则 $a$ 的取值范围是（  ）",
         "options": ["A. $(-2,2)$", "B. $[-2,2]$", "C. $(-\\infty,2)$", "D. $(2,+\\infty)$"], "answer": "A",
         "answer_analysis": "极大值 2、极小值 -2；水平线 $y=a$ 与图象有三个交点当且仅当 $-2<a<2$。",
         "difficulty": "hard", "variant_note": "从求极值升级到方程根的个数", "source": "user_variant", "verified": True},
    ]
    conv("举一反三 · 三次函数变式链", False, 1, [
        {"role": "user", "text": "基于刚才的极值题，来 3 道难度递进的变式。"},
        {"role": "assistant", "blocks": [
            {"type": "markdown", "content": "基于你刚才那道题，给你 3 道难度递进的变式——一道一道来，直接点选项作答，我马上判分 👇"},
            {"type": "card", "data": {"type": "quiz_set", "chain": "variant", "chain_id": "demo-chain-001", "chain_total": 3,
                "source": "user_variant", "items": variant_items}},
        ], "skill": "smart_quiz"},
    ])

    if figure:
        conv("可视化讲解 · 导数与极值", False, 2, [
            {"role": "user", "text": "为什么 $x=-1$ 是极大值点？能不能画图给我看？"},
            {"role": "assistant", "blocks": [
                {"type": "markdown", "content": "看这张图：曲线在 $x=-1$ 之前上升、之后下降，切线斜率（导数）由正变负，所以这里是极大值点。"},
                {"type": "figure", **figure},
                {"type": "markdown", "content": "同理，$x=1$ 处导数由负变正，是极小值点。你可以点「▶ 演示」逐帧看标注出现。"},
            ], "skill": "socratic_solver"},
        ])

    # 错题本录入：真实错题记录 + 一条对话回执（action 为顶层块，前端渲染按钮卡）
    conv("错题本录入 · 极值点判断", False, 3, [
        {"role": "user", "text": "把这道题加入错题本：已知 $f(x)=x^3-3x+1$，求极值。我错在只算了 $f'(x)=0$。"},
        {"role": "assistant", "blocks": [
            {"type": "markdown", "content": ERRBOOK_CONFIRM_TEXT},
            {"type": "action", "data": {"kind": "open_page", "to": "/student/error-book", "label": "去错题本复习", "params": ""}},
        ], "skill": "chat"},
    ])
    # 已有演示会话里的历史消息同步为新文案（幂等）
    db.exec(
        "math",
        f"""UPDATE messages SET content={lit(ERRBOOK_CONFIRM_TEXT)},
              envelope=jsonb_set(
                jsonb_set(envelope, '{{blocks,0,content}}', to_jsonb({lit(ERRBOOK_CONFIRM_TEXT)}::text)),
                '{{blocks,1,type}}', to_jsonb('action'::text)
              )
            WHERE conversation_id IN (
              SELECT c.id FROM conversations c JOIN users u ON u.id=c.user_id
              WHERE u.id={lit(uid)}::uuid AND c.title='错题本录入 · 极值点判断'
            ) AND role='assistant' AND client_msg_id LIKE 'demo-show-%';""",
    )
    db.exec(
        "math",
        f"""UPDATE error_records SET note={lit(ERRBOOK_NOTE_REASON)},
              source_channel='chat_command', origin='chat_quiz'
            WHERE user_id={lit(uid)}::uuid AND question_text LIKE '已知函数 $f(x)=x^3-3x+1$%';""",
    )


def repair_demo_message_chains(db: Db, uid: str) -> None:
    """把演示会话整理成 parent_id 线性链。

    `/conversations/{id}/messages` 只回放活动分支；历史演示数据若 parent_id 全为 NULL，
    前端只能看到最后一条 assistant 消息（引导选项因此丢失）。
    """
    titles = [
        "导数的几何意义怎么理解", "分析我的薄弱点", "三角函数化简总出错",
        "正方体外接球半径", "本周学习复盘", "引导式解题 · 三次函数极值",
        "举一反三 · 三次函数变式链", "可视化讲解 · 导数与极值", "错题本录入 · 极值点判断",
    ]
    for title in titles:
        cid = db.one(
            "math",
            f"SELECT id FROM conversations WHERE user_id={lit(uid)}::uuid AND title={lit(title)} AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1;",
        )
        if not cid:
            continue
        db.exec(
            "math",
            f"""WITH ordered AS (
                  SELECT id, lag(id) OVER (ORDER BY created_at, id) AS prev
                  FROM messages
                  WHERE conversation_id={lit(cid)}::uuid AND deleted_at IS NULL
                )
                UPDATE messages m SET parent_id=ordered.prev, superseded_at=NULL
                FROM ordered WHERE m.id=ordered.id;""",
        )


def seed_practice_quiz(db: Db, uid: str, kps: list[tuple[str, str]]) -> None:
    title = "今日练习 · 导数与函数"
    qid = db.one(
        "math",
        f"SELECT id FROM quizzes WHERE user_id={lit(uid)}::uuid AND title={lit(title)} AND deleted_at IS NULL LIMIT 1;",
    )
    if not qid:
        qid = str(uuid.uuid4())
        db.exec(
            "math",
            f"""INSERT INTO quizzes (id, user_id, source, title, kp_codes, status)
                VALUES ({lit(qid)}::uuid, {lit(uid)}::uuid, 'daily', {lit(title)}, {json_lit([k for k, _ in kps[:3]])}, 'active');""",
        )
        for i, q in enumerate(ASSIGNMENT_QUESTIONS[:5], start=1):
            db.exec(
                "math",
                f"""INSERT INTO quiz_items
                  (id, quiz_id, item_no, q_type, question_text, options, answer, answer_analysis, kp_code, difficulty, ai_generated, source)
                VALUES (gen_random_uuid(), {lit(qid)}::uuid, {i}, {lit(q['q_type'])}, {lit(q['question_text'])},
                  {json_lit(q.get('options')) if q.get('options') else 'NULL'}, {lit(q['answer'])}, {lit(q.get('analysis'))},
                  {lit(pick_kp(kps, i-1))}, {lit(q['difficulty'])}, false, 'demo');""",
            )
    db.exec(
        "math",
        f"""INSERT INTO daily_questions (user_id, date, quiz_id)
            VALUES ({lit(uid)}::uuid, current_date, {lit(qid)}::uuid)
            ON CONFLICT (user_id, date) DO NOTHING;""",
    )


def ensure_assignment(api: Api, db: Db, teacher: dict[str, Any], class_id: str, kps: list[tuple[str, str]]) -> str:
    title = "课后作业 · 导数与函数单调性"
    existing = db.one(
        "math",
        f"SELECT id FROM assignments WHERE creator_id={lit(teacher['id'])}::uuid AND class_id={lit(class_id)}::uuid AND title={lit(title)} AND deleted_at IS NULL LIMIT 1;",
    )
    if existing:
        aid = existing
    else:
        items = []
        for i, q in enumerate(ASSIGNMENT_QUESTIONS):
            q = dict(q)
            q["kp_code"] = pick_kp(kps, i)
            items.append(q)
        data = api.post(
            "/api/teacher/assignments",
            teacher["token"],
            json={
                "class_id": class_id,
                "title": title,
                "client_assignment_id": "demo-assignment-derivative-1",
                "type": "homework",
                "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "quiz_items": items,
            },
        )
        aid = data["assignment_id"]
        api.post(
            f"/api/teacher/assignments/{aid}/publish",
            teacher["token"],
            json={"client_request_id": "demo-publish-derivative-1", "idempotency_key": "demo-publish-derivative-1"},
        )
        log(f"assignment published: {aid}")
    # 第二份未提交作业，用于展示“待完成”
    title2 = "课前预习 · 函数的图象与性质"
    existing2 = db.one(
        "math",
        f"SELECT id FROM assignments WHERE creator_id={lit(teacher['id'])}::uuid AND class_id={lit(class_id)}::uuid AND title={lit(title2)} AND deleted_at IS NULL LIMIT 1;",
    )
    if not existing2:
        qs = [
            {"item_no": 1, "q_type": "choice", "question_text": "函数 $y=2x^2-e^{|x|}$ 在 $[-2,2]$ 上的图象大致为（  ）",
             "options": {"A": "图 A", "B": "图 B", "C": "图 C", "D": "图 D"}, "answer": "A",
             "analysis": "先判断奇偶性，再比较端点与极值。", "kp_code": pick_kp(kps, 2), "difficulty": "hard"},
            {"item_no": 2, "q_type": "choice", "question_text": "函数 $y=\\log_2(x^2-2x-3)$ 的定义域是（  ）",
             "options": {"A": "$(-1,3)$", "B": "$(-\\infty,-1)\\cup(3,+\\infty)$", "C": "$(0,+\\infty)$", "D": "$\\mathbb R$"}, "answer": "B",
             "analysis": "$x^2-2x-3>0$，解得 $x<-1$ 或 $x>3$。", "kp_code": pick_kp(kps, 3), "difficulty": "medium"},
        ]
        data = api.post(
            "/api/teacher/assignments",
            teacher["token"],
            json={"class_id": class_id, "title": title2, "client_assignment_id": "demo-assignment-preclass-1",
                  "type": "pre_class", "deadline": (datetime.now(timezone.utc) + timedelta(days=4)).isoformat(), "quiz_items": qs},
        )
        api.post(f"/api/teacher/assignments/{data['assignment_id']}/publish", teacher["token"],
                 json={"client_request_id": "demo-publish-preclass-1", "idempotency_key": "demo-publish-preclass-1"})
    return aid


def submit_assignment(api: Api, db: Db, student: dict[str, Any], assignment_id: str) -> None:
    existing = db.one(
        "math",
        f"SELECT id FROM submissions WHERE user_id={lit(student['id'])}::uuid AND assignment_id={lit(assignment_id)}::uuid AND deleted_at IS NULL LIMIT 1;",
    )
    if existing:
        return
    data = api.get(f"/api/student/assignments/{assignment_id}", student["token"])
    items = data.get("items") or []
    answers = {1: "B", 2: "D", 3: "C", 4: "(2,-1)", 5: "A"}  # 2 与 5 故意答错
    payload = []
    for it in items:
        no = int(it.get("item_no") or 0)
        payload.append({"item_no": no, "q_type": it.get("q_type") or "choice", "answer_text": answers.get(no, "")})
    api.post(
        "/api/student/practice/submit",
        student["token"],
        json={"assignment_id": assignment_id, "items": payload, "client_submit_id": "demo-assignment-submit-1"},
    )
    log("student assignment submitted (2 wrong answers)")


ERRBOOK_CONFIRM_TEXT = (
    "已经帮你把这道题加入错题本。记录的错因：只算了 $f'(x)=0$，"
    "没有检查两侧导数是否变号。复习计划已排好，随时可以打开错题本复习。"
)
ERRBOOK_NOTE_REASON = "只算了 $f'(x)=0$，没有检查两侧导数是否变号。"

COURSEWARE_SLIDES = [
    {"id": "s1", "type": "title", "title": "函数的单调性：从图像到导数", "content": "人教A版 必修一 · 导数",
     "layout_hint": "title", "elements": [],
     "speaker_note": "同学们好，今天我们从图像直观出发，逐步建立导数与单调性的联系。"},
    {"id": "s2", "type": "concept", "title": "先看图像：增减趋势", "content": "",
     "layout_hint": "concept",
     "elements": [
         {"role": "definition", "title": "单调递增",
          "text": "在区间 I 上，若对任意 $x_1<x_2$，都有 $f(x_1)<f(x_2)$，则称 $f(x)$ 在 I 上单调递增。"},
         {"role": "bullets", "items": ["图像从左到右持续上升", "$x$ 增大时，函数值随之增大", "单调递减时不等号方向相反"]},
     ],
     "speaker_note": "先不引入导数，让学生用自己的语言描述“上升”和“下降”。"},
    {"id": "s3", "type": "derivation", "title": "再看切线：导数的符号", "content": "",
     "layout_hint": "derivation",
     "elements": [
         {"role": "formula", "latex": "f'(x)>0 \\;\\Rightarrow\\; f(x)\\text{ 在 }I\\text{ 上单调递增}"},
         {"role": "formula", "latex": "f'(x)<0 \\;\\Rightarrow\\; f(x)\\text{ 在 }I\\text{ 上单调递减}"},
         {"role": "bullets", "items": ["切线斜率为正，图像上升", "切线斜率为负，图像下降", "导数为 0 的点需要单独判断"]},
     ],
     "speaker_note": "把“切线向上”与“函数递增”对应起来，建立几何直观。"},
    {"id": "s4", "type": "worked-example", "title": "例题：f(x)=x³-3x", "content": "",
     "layout_hint": "worked-example",
     "elements": [
         {"role": "steps", "items": [
             "求导：$f'(x)=3x^2-3=3(x-1)(x+1)$",
             "令 $f'(x)=0$，得 $x=-1$ 或 $x=1$",
             "判断符号：$(-\\infty,-1)$ 为正，$(-1,1)$ 为负，$(1,+\\infty)$ 为正",
             "写出单调区间：增区间 $(-\\infty,-1)$、$(1,+\\infty)$，减区间 $(-1,1)$",
         ]},
         {"role": "note", "text": "易错点：先看定义域，再讨论导数符号。"},
     ],
     "speaker_note": "请学生先自己求导，再同桌互查因式分解。"},
    {"id": "s5", "type": "summary", "title": "当堂检测", "content": "",
     "layout_hint": "summary",
     "elements": [
         {"role": "definition", "title": "结论",
          "text": "$f'(x_0)=0$ 只是极值的必要条件，还要看导数在 $x_0$ 两侧是否变号。"},
         {"role": "bullets", "items": ["反例：$f(x)=x^3$ 在 $x=0$ 处导数为 0，但不是极值点", "先找驻点，再看两侧符号", "单调区间必须写成区间形式"]},
         {"role": "note", "text": "课堂检测：判断 $f'(x_0)=0$ 是否一定是极值点。"},
     ],
     "speaker_note": "用反例 f(x)=x³ 收束，强调“导数为 0”与“极值”不等价。"},
]

COURSEWARE_DECK_ID = "demo-deck-monotonicity-v4"
COURSEWARE_DECK_REVISION = 4


def courseware_slides(figure: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把课件页与真实确定性渲染的函数图打包为发布载荷（role 契约 + 资产通道）。"""
    import copy

    slides = copy.deepcopy(COURSEWARE_SLIDES)
    frames = (figure or {}).get("frames") or []
    if not frames:
        return slides

    def asset(frame: dict[str, Any], asset_id: str) -> dict[str, str]:
        uri = str(frame.get("data_uri") or "")
        data = uri.split(",", 1)[1] if "," in uri else ""
        return {"id": asset_id, "mime_type": "image/svg+xml", "data_base64": data}

    if len(frames) >= 1:
        slides[1]["elements"].insert(
            0, {"role": "figure", "text": "观察曲线从左到右的上升与下降"}
        )
        slides[1]["assets"] = [asset(frames[0], "demo-fig-curve")]
    if len(frames) >= 2:
        slides[3]["elements"].insert(
            0, {"role": "figure", "text": "f(x)=x³-3x 的图象与极值点"}
        )
        slides[3]["assets"] = [asset(frames[1], "demo-fig-extrema")]
    return slides


def ensure_courseware(api: Api, db: Db, teacher: dict[str, Any], class_id: str, student: dict[str, Any]) -> str | None:
    title = "函数的单调性：从图像到导数"
    data = api.get(f"/api/teacher/classroom-coursewares?class_id={class_id}", teacher["token"])
    items = (data or {}).get("items") or []
    target = None
    for item in items:
        if item.get("title") != title:
            continue
        if (
            item.get("deck_id") == COURSEWARE_DECK_ID
            and item.get("deck_revision") == COURSEWARE_DECK_REVISION
            and item.get("status") in ("draft", "confirmed", "published")
        ):
            target = item
            continue
        # 旧版课件（schema 不兼容 / 空白页）下架，避免教师与学生列表出现两份
        try:
            api.post(
                f"/api/teacher/classroom-coursewares/{item['courseware_id']}/archive",
                teacher["token"],
            )
            log(f"courseware archived: {item['courseware_id']}")
        except Exception as e:
            log(f"courseware archive skipped: {str(e)[:100]}")
    if target is None:
        created = api.post(
            "/api/teacher/classroom-coursewares",
            teacher["token"],
            json={
                "class_id": class_id, "deck_id": COURSEWARE_DECK_ID, "deck_revision": COURSEWARE_DECK_REVISION,
                "title": title, "textbook_line": "人教A版 必修一 · 导数", "subject": "数学",
                "slides": courseware_slides(ensure_figure_payload()), "source_refs": [],
            },
        )
        cw_id = created["courseware_id"]
        log(f"courseware created: {cw_id}, waiting for prepare...")
    else:
        cw_id = target["courseware_id"]
    for _ in range(50):
        detail = api.get(f"/api/teacher/classroom-coursewares/{cw_id}", teacher["token"])
        if detail.get("status") in ("published", "archived"):
            break
        time.sleep(2)
    # 学生打开课件并留下进度
    student_items = api.get("/api/student/classroom-coursewares", student["token"]).get("items") or []
    target = next((x for x in student_items if x.get("courseware_id") == cw_id), None)
    if target and not target.get("last_session_id"):
        opened = api.post(f"/api/student/classroom-coursewares/{cw_id}/open", student["token"])
        sid = opened.get("session_id")
        if sid:
            try:
                api.patch(f"/api/classroom/sessions/{sid}/progress", student["token"],
                          json={"slide_index": 3, "page_check": {"0": "ok", "1": "ok", "2": "ok"}})
            except Exception as e:
                log(f"courseware progress skipped: {str(e)[:100]}")
    return cw_id


# ---------------------------------------------------------------------------
# Teacher grading workspace (8100)
# ---------------------------------------------------------------------------

GRADING_QUESTIONS = [
    {"q_no": 1, "kp_code": "导数", "stem_latex": "已知 $f(x)=x^3-3x$，求 $f(x)$ 的极值。",
     "reference_answer": "极大值 f(-1)=2，极小值 f(1)=-2", "full_score": 10},
    {"q_no": 2, "kp_code": "导数", "stem_latex": "证明：若 $f'(x)>0$ 在区间 I 上恒成立，则 $f(x)$ 在 I 上单调递增。",
     "reference_answer": "任取 x1<x2，由拉格朗日中值定理存在 ξ∈(x1,x2) 使 f(x2)-f(x1)=f'(ξ)(x2-x1)>0", "full_score": 12},
    {"q_no": 3, "kp_code": "立体几何", "stem_latex": "正方体棱长为 2，求其外接球的半径与表面积。",
     "reference_answer": "R=√3，S=12π", "full_score": 10},
]

GRADING_ANSWERS = {
    "小林": ["极大值 2，极小值 -2", "任取两点，用中值定理可得差为正，所以递增", "R=√3，S=12π"],
    "小陈": ["极值是 2 和 -2", "因为导数大于 0，所以函数递增", "R=2，S=16π"],
    "小周": ["极大值 f(-1)=2，极小值 f(1)=-2", "用拉格朗日中值定理，f'(ξ)>0，所以 f(x2)>f(x1)", "外接球半径 √3，表面积 12π"],
    "小许": ["极大值 2", "", "半径是 √3"],
}


def ensure_grading(api: Api, db: Db, teacher: dict[str, Any], class_id: str) -> str | None:
    try:
        listing = api.teacher("GET", "/grading/assignments", teacher["token"])
        items = listing.get("items") if isinstance(listing, dict) else listing
        title = "课后作业 · 导数与函数单调性"
        existing = next((x for x in (items or []) if x.get("title") == title), None)
        if existing:
            gid = existing.get("id") or existing.get("assignment_id")
        else:
            created = api.teacher("POST", "/assignments/publish", teacher["token"], json={
                "title": title, "class_id": class_id, "questions": GRADING_QUESTIONS,
            })
            gid = created.get("assignment_id")
            log(f"grading assignment created: {gid}")
        if not gid:
            return None
        detail = api.teacher("GET", f"/grading/assignments/{gid}", teacher["token"])
        questions = detail.get("questions") or []
        # 给 4 位学生写入真实作答
        for name, answers in GRADING_ANSWERS.items():
            for i, q in enumerate(questions):
                qid = q.get("id") or q.get("question_id")
                if not qid or i >= len(answers) or not answers[i]:
                    continue
                try:
                    api.teacher("POST", f"/grading/assignments/{gid}/answers", teacher["token"], json={
                        "question_id": qid, "student_name": name, "answer_text": answers[i],
                    })
                except Exception as e:
                    log(f"grading answer {name} q{i+1}: {str(e)[:100]}")
        # 对已有聚类做一次终审，让“已批改”不是 0
        detail = api.teacher("GET", f"/grading/assignments/{gid}", teacher["token"])
        for q in (detail.get("questions") or []):
            for c in (q.get("clusters") or []):
                cid = c.get("id") or c.get("cluster_id")
                if not cid or c.get("confirmed"):
                    continue
                try:
                    api.teacher("POST", f"/grading/assignments/{gid}/clusters/{cid}/confirm", teacher["token"],
                                json={"default_score": 8 if c.get("kind") != "wrong" else 3, "feedback": "按步骤给分，注意关键条件。"})
                except Exception:
                    pass
        return str(gid)
    except Exception as e:
        log(f"grading seed skipped: {str(e)[:160]}")
        return None


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

RESEARCH_PROJECTS = [
    {
        "name": "多模态数学推理中的证据链",
        "description": "围绕视觉-语言模型在几何证明中的可验证推理，整理文献、实验记录与论文草稿。",
        "papers": [0, 3],
        "files": {
            "main.tex": r"""\documentclass{article}
\usepackage{amsmath,amssymb}
\title{Verifiable Evidence Chains for Multimodal Mathematical Reasoning}
\author{Lin Zhiyuan}
\begin{document}
\maketitle
\section{Introduction}
We study whether each step of a geometric proof can be checked locally and traced to a source span.
\section{Method}
For a claim $s_i$, we require a local justification and an evidence anchor $e_i$:
\begin{equation}
  s_i \models s_{i+1}, \qquad \mathrm{anchor}(s_i)=e_i.
\end{equation}
\section{Discussion}
Verifiability does not imply correctness; undeclared assumptions must remain visible.
\end{document}
""",
            "refs.bib": "@article{lin2026evidence,\n  title={Verifiable Evidence Chains for Multimodal Mathematical Reasoning},\n  author={Lin, Zhiyuan and Chen, Yu},\n  year={2026}\n}\n",
            "notes.md": "# 研究笔记\n\n- 证据链的最小单位：局部理由 + 原文锚点。\n- 失败案例集中在隐式定义域假设。\n",
        },
        "notes": [
            ("证据链的核心不是最终答案", "每个中间 claim 都需要可检查的局部理由；仅验证最终答案会掩盖推理跳跃。", 1),
            ("隐式假设是主要失败源", "几何证明里的“显然”往往依赖未声明的定义域或位置关系，需要在证据卡中显式列出。", 2),
        ],
    },
    {
        "name": "高中数学错因图谱与个性化练习",
        "description": "把错题、BKT 掌握度与 FSRS 复习排期连成一条可解释的个性化练习链路。",
        "papers": [1, 4],
        "files": {
            "main.tex": r"""\documentclass{article}
\usepackage{amsmath}
\title{An Explainable Error-Graph for Personalized Mathematics Practice}
\author{Chen Yu}
\begin{document}
\maketitle
\section{Motivation}
Practice recommendation should explain why a topic is chosen now, not only what is weak.
\section{Model}
We combine BKT mastery $m_k$, FSRS retrievability $R_k(t)$ and error subtype frequency.
\section{Evaluation}
Ablating the explanation layer reduces student trust even when accuracy is unchanged.
\end{document}
""",
            "refs.bib": "@article{chen2026error,\n  title={An Explainable Error-Graph for Personalized Mathematics Practice},\n  author={Chen, Yu and Zhou, Ming},\n  year={2026}\n}\n",
        },
        "notes": [
            ("推荐必须带依据", "首页只给“练这个”不够，还要给出 BKT 掌握度、FSRS 到期或错因归因。", 1),
            ("错因图谱应连到题目", "错因不是标签集合，要能回到具体题干、答案与复习记录。", 3),
        ],
    },
    {
        "name": "证据锚定的 LaTeX 学术写作",
        "description": "研究如何在 LaTeX 写作过程中保留引用、证据与修改的可追溯性。",
        "papers": [2, 5],
        "files": {
            "main.tex": r"""\documentclass{article}
\usepackage{amsmath}
\title{Evidence-Anchored LaTeX Writing for Research Teams}
\author{Lin Zhiyuan}
\begin{document}
\maketitle
\section{Introduction}
Writing and reading should share one project context. Every external claim should keep a citation anchor.
\section{Workflow}
The writer selects a paragraph, asks the agent to verify or rewrite, and receives a diff with a scope.
\section{Conclusion}
Evidence anchors make review faster and reduce silent edits.
\end{document}
""",
            "refs.bib": "@article{lin2026writing,\n  title={Evidence-Anchored LaTeX Writing for Research Teams},\n  author={Lin, Zhiyuan and Xu, Ran},\n  year={2026}\n}\n",
        },
        "notes": [
            ("改写必须先出 diff", "任何 AI 对论文段落的修改都要显式确认，不能静默覆盖原稿。", 1),
            ("引用和原文要双向回跳", "证据角标点击应回到页码与高亮段落，写作时也能看到来源。", 4),
        ],
    },
]


def ensure_research(api: Api, db: Db, researcher: dict[str, Any], pdfs: list[Path]) -> list[dict[str, Any]]:
    existing = api.research("GET", "/projects", researcher["token"]).get("items") or []
    by_name = {p.get("name"): p for p in existing}
    out = []
    for idx, spec in enumerate(RESEARCH_PROJECTS):
        p = by_name.get(spec["name"])
        if not p:
            p = api.research("POST", "/projects", researcher["token"], json={
                "name": spec["name"], "description": spec["description"], "client_request_id": f"demo-project-{idx}-2026",
            }).get("project")
            log(f"research project created: {p['id']} {spec['name']}")
        pid = p["id"]
        # files
        for path, content in spec["files"].items():
            try:
                api.research("GET", f"/projects/{pid}/files/{path}", researcher["token"])
            except Exception:
                try:
                    kind = "tex" if path.endswith(".tex") else ("bib" if path.endswith(".bib") else "data")
                    api.research("POST", f"/projects/{pid}/files", researcher["token"],
                                 json={"path": path, "kind": kind, "content": content})
                except Exception as e:
                    log(f"file {path} skipped: {str(e)[:100]}")
        # papers：按标题补齐缺的篇目（不是“已有一篇就全跳过”）
        papers = api.research("GET", f"/projects/{pid}/papers", researcher["token"]).get("items") or []
        paper_ids = [x.get("id") for x in papers if x.get("id")]
        existing_titles = {str(x.get("title") or "").lower() for x in papers}
        for pi in spec["papers"]:
            title, authors = PAPERS[pi]
            if any(title.lower() in t for t in existing_titles):
                continue
            try:
                with pdfs[pi].open("rb") as fh:
                    r = api.session.post(
                        f"{api.base}/api/research-vnext/projects/{pid}/papers",
                        headers={"Authorization": f"Bearer {researcher['token']}"},
                        files={"file": (Path(pdfs[pi]).name, fh, "application/pdf")},
                        timeout=180,
                    )
                if r.status_code < 400:
                    data = r.json()
                    paper = data.get("paper") or data.get("data", {}).get("paper")
                    if paper and paper.get("id"):
                        paper_ids.append(paper["id"])
                        # 元数据解析以文件名为准时，补成论文标题/作者/年份（真实上传资产的展示元数据）
                        db.exec(
                            "research",
                            f"""UPDATE research_paper SET title={lit(title)}, authors={json_lit([a.strip() for a in authors.split(',')])},
                                  year=2026, venue='arXiv preprint' WHERE id={lit(paper['id'])}::uuid;""",
                        )
                        log(f"paper uploaded: {title}")
                else:
                    log(f"paper upload failed {pdfs[pi].name}: {r.status_code} {r.text[:160]}")
            except Exception as e:
                log(f"paper upload error {pdfs[pi].name}: {str(e)[:160]}")
        # notes
        existing_notes = api.research("GET", f"/projects/{pid}/notes", researcher["token"]).get("items") or []
        existing_titles = {n.get("title") for n in existing_notes}
        for title, body, page in spec["notes"]:
            if title in existing_titles:
                continue
            payload = {
                "title": title, "body": body,
                "paper_id": paper_ids[0] if paper_ids else None,
                "page": page, "section": "Method" if page > 1 else "Introduction",
                "selected_text": body[:80], "xref_kind": "paper",
            }
            try:
                api.research("POST", f"/projects/{pid}/notes", researcher["token"], json=payload)
            except Exception as e:
                log(f"note skipped: {str(e)[:100]}")
        # 科研对话与 Agent 结果：直接写 research_vnext（走真实 UI 投影）
        tenant_id = p.get("tenant_id") or db.one("research", f"SELECT tenant_id FROM research_project WHERE id={lit(pid)}::uuid;")
        owner_id = p.get("owner_id") or researcher["id"]
        seed_research_thread(db, pid, tenant_id, owner_id, idx, spec)
        out.append({"id": pid, "name": spec["name"], "papers": paper_ids})
    return out


def seed_research_thread(db: Db, pid: str, tenant_id: str, owner_id: str, idx: int, spec: dict[str, Any]) -> None:
    session_id = db.one(
        "research",
        f"SELECT id FROM research_session WHERE project_id={lit(pid)}::uuid AND owner_id={lit(owner_id)}::uuid AND title='科研对话' LIMIT 1;",
    )
    if not session_id:
        session_id = str(uuid.uuid4())
        db.exec(
            "research",
            f"""INSERT INTO research_session
              (id, tenant_id, project_id, owner_id, title, mode, goal, status, revision, created_at, updated_at,
               current_artifact_refs, summary_ref, policy_refs, confirmation_count)
            VALUES ({lit(session_id)}::uuid, {lit(tenant_id)}::uuid, {lit(pid)}::uuid, {lit(owner_id)}::uuid,
              '科研对话', 'MODELING', '', 'DRAFT', 0, now() - interval '{idx + 2} day', now() - interval '{idx + 1} day',
              '[]'::jsonb, '', '[]'::jsonb, 0);""",
        )
        q = "这篇文章的核心方法是什么？它和普通的链式推理有什么区别？"
        a = (
            "文章把“可验证”拆成两步：每一步先给出局部理由，再把外部知识绑定到原文证据锚点。"
            "普通链式推理只检查最终答案，因而无法发现中间步骤的隐含假设。"
            "这篇工作要求每条 claim 都带一个可回跳的来源，未声明的定义域或位置关系会被标成“需要补充条件”。"
        )
        for seq, (role, text) in enumerate((("user", q), ("assistant", a)), start=1):
            db.exec(
                "research",
                f"""INSERT INTO research_message
                  (id, session_id, tenant_id, role, text, payload, evidence_refs, seq, created_at, trace_id, client_request_id)
                VALUES ({lit(str(uuid.uuid4()))}::uuid, {lit(session_id)}::uuid, {lit(tenant_id)}::uuid, {lit(role)},
                  {lit(text)}, '{{}}'::jsonb, '[]'::jsonb, {seq}, now() - interval '{idx + 2} day' + interval '{seq * 2} minute',
                  '', {lit(f'demo-research-{idx}-{seq}')});""",
            )
    # 已完成 Agent 任务（UI 的“恢复上次结果”会显示在对话线程）
    existing_task = db.one(
        "research",
        f"SELECT id FROM research_agent_task WHERE project_id={lit(pid)}::uuid AND preset='derivation_check' LIMIT 1;",
    )
    if not existing_task:
        result = {
            "kind": "derivation_check",
            "status": "supported",
            "summary": "证据链方法成立，但需要把“局部可验证”和“结论正确”分开表述。",
            "findings": [
                {"label": "证据锚点", "detail": "每个外部 claim 都应带来源页码/段落，当前设计满足。", "severity": "info"},
                {"label": "隐含假设", "detail": "定义域与位置关系必须显式写入假设清单，否则形式可验证也可能语义错误。", "severity": "warning"},
            ],
            "evidence": [],
            "source_locations": [],
            "patches": [],
            "actions": [],
            "confidence": 0.86,
        }
        db.exec(
            "research",
            f"""INSERT INTO research_agent_task
              (id, tenant_id, project_id, actor_id, preset, kind, context, status, result, created_at, updated_at, finished_at)
            VALUES ({lit(str(uuid.uuid4()))}::uuid, {lit(tenant_id)}::uuid, {lit(pid)}::uuid, {lit(owner_id)}::uuid,
              'derivation_check', 'derivation_check', '{{}}'::jsonb, 'completed', {json_lit(result)},
              now() - interval '{idx + 1} day', now() - interval '{idx + 1} day', now() - interval '{idx + 1} day');""",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), default="local")
    args = parser.parse_args()
    prof = PROFILES[args.profile]
    api = Api(prof["base"], prof["teacher_base"])
    db = Db(prof)
    log(f"profile={args.profile} base={prof['base']}")

    pdfs = ensure_pdfs()
    log(f"pdfs ready: {len(pdfs)}")

    student = ensure_user(api, db, DEMO["student"]["phone"], "student", DEMO["student"]["nickname"], "杭州市第二中学")
    teacher = ensure_user(api, db, DEMO["teacher"]["phone"], "teacher", DEMO["teacher"]["nickname"], "杭州市第二中学")
    researcher = ensure_user(api, db, DEMO["researcher"]["phone"], "researcher", DEMO["researcher"]["nickname"], "浙江大学数学科学学院")
    classmates = [ensure_user(api, db, c["phone"], "student", c["nickname"], "杭州市第二中学") for c in DEMO["classmates"]]
    log("accounts ready")

    klass = ensure_class(api, db, teacher, [student, *classmates])
    log(f"class ready: {klass['name']} / {klass['invite']}")

    kps = seed_student_learning(db, student["id"])
    seed_errors(db, student["id"], kps)
    seed_conversations(db, student["id"])
    figure = ensure_figure_payload()
    seed_showcase_conversations(db, student["id"], figure)
    repair_demo_message_chains(db, student["id"])
    seed_practice_quiz(db, student["id"], kps)
    upload_kb(api, db, student)
    log("student learning data ready (dialogue / socratic / variants / visual / errors)")

    assignment_id = ensure_assignment(api, db, teacher, klass["id"], kps)
    submit_assignment(api, db, student, assignment_id)
    # 作业提交会按 BKT 回写掌握度；在提交后重新落一次展示基线，保证首页有真实的强弱分布。
    seed_student_learning(db, student["id"])
    ensure_grading(api, db, teacher, klass["id"])
    ensure_courseware(api, db, teacher, klass["id"], student)
    log("teacher / classroom scenario ready")

    projects = ensure_research(api, db, researcher, pdfs)
    log(f"research projects ready: {len(projects)}")

    summary = {
        "profile": args.profile,
        "base": prof["base"],
        "password": PASSWORD,
        "accounts": {
            "student": {"phone": student["phone"], "nickname": student["nickname"], "role": "student"},
            "teacher": {"phone": teacher["phone"], "nickname": teacher["nickname"], "role": "teacher"},
            "researcher": {"phone": researcher["phone"], "nickname": researcher["nickname"], "role": "researcher"},
        },
        "class": klass,
        "assignment_id": assignment_id,
        "research_projects": projects,
    }
    out = SEED_DIR / f"summary-{args.profile}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"summary written: {out}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
