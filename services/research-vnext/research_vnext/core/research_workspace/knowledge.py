"""RV17 · 项目知识检索（P0-6）。

检索**项目知识**：文献（`research_paper`）+ 笔记（`research_note`）。

铁律 D4：**禁止引入 pgvector / embedding**（教室平台 768 维、通用 API 1024 维，
禁止交叉写索引；8300 无向量设施）。只用 PG 原生能力：

1. `to_tsvector('simple', <token 列>) @@ to_tsquery('simple', <query tsquery>)`
   —— CJK 走 **2-gram**、拉丁走词（`search_tokenize.py`，索引/查询同源）；
2. `websearch_to_tsquery('simple', q)` 对原始文本的英文词兜底（保持英文不回归）；
3. `ILIKE` 子串兜底（对精确子串、短查询稳健）；
4. `pg_trgm` 的 `similarity()` 仅用于**排序**（不单独作为命中条件）。

响应披露「基于当前项目 N 篇文献、M 条笔记」（宪法 §45）。结果项带 `kind`
（`paper` / `note`）区分来源；笔记项保留 `note_id`，点击可回到其 locator。
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import db_schema
from .search_tokenize import query_tsquery

_MAX_SNIPPET = 260

#: 检索模式（如实披露：全文 = CJK 2-gram + 拉丁词；+= trigram 排序）。
RETRIEVAL_MODE = "fulltext+cjk_bigram+trigram"


def _clip_text(txt: str, query: str, width: int = _MAX_SNIPPET) -> str:
    """在文本里找命中片段并裁切（找得到 → 命中周围，找不到 → 前缀）。"""
    s = txt or ""
    if not s:
        return ""
    terms = [t for t in query.split() if t] or ([query] if query else [])
    low = s.lower()
    for term in terms:
        idx = low.find(term.lower())
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(s), idx + len(term) + width)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(s) else ""
            return f"{prefix}{s[start:end].strip()}{suffix}"
    return s[:width] + ("…" if len(s) > width else "")


def _snippet_from_pages(pages: list, query: str) -> tuple[str, int, str]:
    """在已解析页块里找命中片段，返回 `(snippet, page, section)`。"""
    terms = [t for t in query.split() if t] or [query]
    lowered = [t.lower() for t in terms]
    fallback: tuple[str, int, str] | None = None
    for page in pages or []:
        pno = int(page.get("page") or 0)
        for block in page.get("blocks") or []:
            txt = str(block.get("text") or "")
            low = txt.lower()
            if fallback is None and txt.strip():
                fallback = (txt, pno, str(block.get("section") or ""))
            for term in lowered:
                idx = low.find(term)
                if idx >= 0:
                    start = max(0, idx - 60)
                    end = min(len(txt), idx + len(term) + _MAX_SNIPPET)
                    snip = txt[start:end].strip()
                    prefix = "…" if start > 0 else ""
                    suffix = "…" if end < len(txt) else ""
                    return f"{prefix}{snip}{suffix}", pno, str(block.get("section") or "")
    if fallback is not None:
        txt, pno, sec = fallback
        return (txt[:_MAX_SNIPPET] + ("…" if len(txt) > _MAX_SNIPPET else "")), pno, sec
    return "", 0, ""


def _counts(db: Session, schema: str, tenant_id: uuid.UUID, project_id: uuid.UUID) -> tuple[int, int]:
    papers_total = db.execute(
        text(
            f"SELECT count(*) FROM {schema}.research_paper "
            "WHERE tenant_id = :tid AND project_id = :pid AND deleted_at IS NULL"
        ),
        {"tid": str(tenant_id), "pid": str(project_id)},
    ).scalar() or 0
    notes_total = db.execute(
        text(
            f"SELECT count(*) FROM {schema}.research_note "
            "WHERE tenant_id = :tid AND project_id = :pid AND deleted_at IS NULL"
        ),
        {"tid": str(tenant_id), "pid": str(project_id)},
    ).scalar() or 0
    return int(papers_total), int(notes_total)


def search(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    query: str,
    limit: int = 10,
) -> dict:
    """项目知识检索（PG 全文[CJK 2-gram+拉丁] + ILIKE 兜底 + trigram 排序）。

    返回 `{query, items, based_on, retrieval_mode}`；`items` 混合文献项
    （`kind=paper`）与笔记项（`kind=note`），按 score 降序。
    """
    q = (query or "").strip()
    lim = max(1, min(int(limit or 10), 50))
    schema = db_schema()

    papers_total, notes_total = _counts(db, schema, tenant_id, project_id)
    based_on = {"papers": papers_total, "notes": notes_total}

    if not q:
        return {"query": q, "items": [], "based_on": based_on, "retrieval_mode": RETRIEVAL_MODE}

    tsq = query_tsquery(q)
    params: dict = {
        "tid": str(tenant_id),
        "pid": str(project_id),
        "q": q,
        "like": f"%{q}%",
        "lim": lim,
    }

    # token 分支仅在查询可切出词元时启用（to_tsquery('simple','') 会报错）。
    if tsq:
        params["tsq"] = tsq
        paper_tok_hit = " OR to_tsvector('simple', p.search_tokens) @@ to_tsquery('simple', :tsq)"
        note_tok_hit = " OR to_tsvector('simple', n.search_tokens) @@ to_tsquery('simple', :tsq)"
        paper_tok_rank = (
            "ts_rank_cd(to_tsvector('simple', p.search_tokens), to_tsquery('simple', :tsq))"
        )
        note_tok_rank = (
            "ts_rank_cd(to_tsvector('simple', n.search_tokens), to_tsquery('simple', :tsq))"
        )
    else:
        paper_tok_hit = note_tok_hit = ""
        paper_tok_rank = note_tok_rank = "0.0"

    paper_sql = text(
        f"""
        SELECT p.id, p.title, p.authors, p.pages, p.parse_status,
               {paper_tok_rank} AS tok_rank,
               ts_rank_cd(to_tsvector('simple', p.search_text),
                          websearch_to_tsquery('simple', :q)) AS tsv_rank,
               similarity(p.search_text, :q) AS trgm_sim
        FROM {schema}.research_paper p
        WHERE p.tenant_id = :tid AND p.project_id = :pid AND p.deleted_at IS NULL
          AND (
                p.search_text ILIKE :like
                OR p.title ILIKE :like
                OR to_tsvector('simple', p.search_text) @@ websearch_to_tsquery('simple', :q)
                {paper_tok_hit}
              )
        ORDER BY ({paper_tok_rank}) * 3.0
                 + ts_rank_cd(to_tsvector('simple', p.search_text),
                              websearch_to_tsquery('simple', :q)) * 2.0
                 + similarity(p.search_text, :q) DESC
        LIMIT :lim
        """
    )
    note_sql = text(
        f"""
        SELECT n.id, n.title, n.body, n.paper_id, n.page, n.section, n.selected_text,
               {note_tok_rank} AS tok_rank,
               similarity(
                   coalesce(n.title, '') || ' ' || coalesce(n.body, '') || ' '
                   || coalesce(n.selected_text, ''), :q
               ) AS trgm_sim
        FROM {schema}.research_note n
        WHERE n.tenant_id = :tid AND n.project_id = :pid AND n.deleted_at IS NULL
          AND (
                n.title ILIKE :like
                OR n.body ILIKE :like
                OR n.selected_text ILIKE :like
                {note_tok_hit}
              )
        ORDER BY ({note_tok_rank}) * 3.0
                 + similarity(
                       coalesce(n.title, '') || ' ' || coalesce(n.body, '') || ' '
                       || coalesce(n.selected_text, ''), :q
                   ) DESC
        LIMIT :lim
        """
    )

    paper_rows = db.execute(paper_sql, params).mappings().all()
    note_rows = db.execute(note_sql, params).mappings().all()

    items: list[dict] = []
    for r in paper_rows:
        snippet, page, section = _snippet_from_pages(list(r["pages"] or []), q)
        score = round(
            float(r["tok_rank"] or 0.0) * 3.0
            + float(r["tsv_rank"] or 0.0) * 2.0
            + float(r["trgm_sim"] or 0.0),
            4,
        )
        items.append(
            {
                "kind": "paper",
                "paper_id": str(r["id"]),
                "title": r["title"] or "",
                "authors": list(r["authors"] or []),
                "page": page,
                "section": section,
                "snippet": snippet,
                "score": score,
                "parse_status": r["parse_status"],
            }
        )
    for r in note_rows:
        score = round(
            float(r["tok_rank"] or 0.0) * 3.0 + float(r["trgm_sim"] or 0.0),
            4,
        )
        items.append(
            {
                "kind": "note",
                "note_id": str(r["id"]),
                "title": r["title"] or "",
                "body": r["body"] or "",
                "paper_id": str(r["paper_id"]) if r["paper_id"] else None,
                "page": r["page"],
                "section": r["section"] or "",
                "selected_text": r["selected_text"] or "",
                "snippet": _clip_text(r["body"] or r["title"] or "", q),
                "score": score,
            }
        )

    items.sort(key=lambda it: it["score"], reverse=True)
    items = items[:lim]

    return {
        "query": q,
        "items": items,
        "based_on": based_on,
        "retrieval_mode": RETRIEVAL_MODE,
    }


__all__ = ["RETRIEVAL_MODE", "search"]
