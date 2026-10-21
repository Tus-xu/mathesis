"""K12-KGraph 数学子图 → kp_nodes 暂存管线（B2 §5.2）。

输入格式（实测自 repos/K12-KGraph/demo/kg/math_7a_rjb.json，见 notes/B2-notes-k12kgraph.md）：
- 节点：{id, label, name, properties}，7 类 label（Book/Chapter/Section/Concept/Skill/Exercise/…）
- 边：{type, source, target, properties{evidence}}，9 类；prerequisites_for 方向 = source 是前置；
  层级靠 is_part_of（Section→Chapter→Book），概念用 appears_in 挂节；多目标边形态
  target_name_to_ids 需展开。

⚠ 数据许可：CC BY-NC-SA 4.0（内部教学自用口径，manifest 已登记）。
⚠ 规模口径：仓库内仅单册 demo（157 节点）；全量数学子图（1475 概念/428 技能）
  在 HuggingFace 发布版——下载后用 --input 指向全量文件重跑即可（管线幂等）。

运行：python -m corpus.pipelines.kp_seed [--input <graph.json>]
输出：corpus/staging/kp_nodes.jsonl + kp_nodes_report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = Path("D:/teacher-research/repos/K12-KGraph/demo/kg/math_7a_rjb.json")
STAGING_DIR = Path(__file__).resolve().parents[1] / "staging"
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "kp_seed.log"

NODE_LABELS = {"Book", "Chapter", "Section", "Concept", "Skill", "Exercise"}
PREREQ_EDGE = "prerequisites_for"
PART_EDGE = "is_part_of"
APPEARS_EDGE = "appears_in"
_EDGE_TYPE = re.compile(r"^[a-z_]+$")


def _id_to_code(node_id: str) -> str:
    """原始节点 id 即 code（稳定流水线）；非法字符归一。"""
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", node_id).strip("_")


def parse(graph: dict, *, book_filter: str | None = None) -> tuple[list[dict], dict]:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or graph.get("links") or []
    if not nodes:
        raise ValueError("图中无 nodes 字段——检查输入格式（实测 demo 为 {nodes, edges}）")

    node_by_id: dict[str, dict] = {}
    for n in nodes:
        node_by_id[n.get("id")] = n

    # 多目标边展开：edges 的 target 可能是 name 引用列表（target_name_to_ids 形态在
    # 实测中表现为同一 type 的多条边/或 target 指向名字而非 id——名字解析到 id）
    name_to_ids: dict[str, list[str]] = {}
    for n in nodes:
        name_to_ids.setdefault(n.get("name"), []).append(n.get("id"))

    prereq: dict[str, list[dict]] = {}
    parts: dict[str, str] = {}      # child -> parent id（is_part_of）
    appears: dict[str, list[str]] = {}  # concept -> section ids
    skipped_edges = 0
    for e in edges:
        etype = str(e.get("type") or "")
        src = e.get("source")
        tgt = e.get("target")
        tgt_ids = [tgt] if tgt in node_by_id else name_to_ids.get(tgt, [])
        if not tgt_ids:
            skipped_edges += 1
            continue
        for t in tgt_ids:
            if etype == PREREQ_EDGE:
                prereq.setdefault(t, []).append({  # t 的前置是 src
                    "code": _id_to_code(src),
                    "evidence": (e.get("properties") or {}).get("evidence"),
                })
            elif etype == PART_EDGE:
                parts[t] = src
            elif etype == APPEARS_EDGE:
                appears.setdefault(src, []).append(t)
            # 其余边类型（references/similar_to…）M1 不入 kp_nodes，留在原数据

    out: list[dict] = []
    label_counts: dict[str, int] = {}
    for n in nodes:
        label = str(n.get("label") or "")
        if label not in NODE_LABELS or label == "Exercise":
            continue  # 习题节点不入 kp_nodes（练习落 quiz_bank）
        if book_filter and not str(n.get("id")).startswith(book_filter):
            continue
        nid = n.get("id")
        sections = appears.get(nid, [])
        # parent：appears 的字典序最小 Section；无 appears 的取 is_part_of
        parent = None
        path: list[str] = []
        if sections:
            parent = min(sections)
        elif nid in parts:
            parent = parts[nid]
        # path：沿 parent 链向上（最多 4 层），name 拼 path
        cur = parent
        chain: list[str] = []
        seen = set()
        while cur and cur not in seen and cur in node_by_id:
            seen.add(cur)
            chain.append(str(node_by_id[cur].get("name") or ""))
            cur = parts.get(cur) or appears.get(cur, [None])[0]
        path = list(reversed([c for c in chain if c]))
        label_counts[label] = label_counts.get(label, 0) + 1
        out.append({
            "code": _id_to_code(nid),
            "name": str(n.get("name") or ""),
            "label": label,
            "parent_code": _id_to_code(parent) if parent else None,
            "path": path,
            "source": "K12-KGraph",
            "metadata": {
                "prerequisites": prereq.get(nid, []),
                "also_in_sections": sections[1:] if len(sections) > 1 else [],
            },
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_nodes": len(nodes),
        "input_edges": len(edges),
        "skipped_edges_unresolved": skipped_edges,
        "output_nodes": len(out),
        "label_counts": label_counts,
    }
    return out, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--book-prefix", default=None, help="只保留该前缀节点（如 math_bx1）")
    args = ap.parse_args(argv)
    graph = json.loads(Path(args.input).read_text(encoding="utf-8"))
    records, report = parse(graph, book_filter=args.book_prefix)

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STAGING_DIR / "kp_nodes.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (STAGING_DIR / "kp_nodes_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as lf:
        lf.write(f"{report['generated_at']} in={report['input_nodes']} out={report['output_nodes']}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
