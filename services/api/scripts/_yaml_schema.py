"""解析队友导出的 8 个工作流 YAML：开始节点入参 + 结束节点出参"""

import sys
import yaml
from pathlib import Path

BASE = Path(r"D:\工作流搭建情况")
FILES = [
    "wf_intent_router.yml",
    "wf_speech_to_latex.yml",
    "wf_smart_quiz.yml",
    "wf_error_analysis.yml",
    "wf_course_preprocess.yml",
    "wf_socratic_cha.yml",
    "wf_doc_understand_V2.yml",
    "wf_solution_pregrade.yml",
]


def node_info(doc):
    nodes = doc.get("flowData", {}).get("nodes", [])
    start, end = None, None
    for n in nodes:
        if n.get("type") == "开始节点":
            start = n
        if n.get("type") == "结束节点":
            end = n
    return start, end


def main():
    for f in FILES:
        p = BASE / f
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[{f}] YAML 解析失败: {e}")
            continue
        start, end = node_info(doc)
        ins = []
        for o in (start or {}).get("data", {}).get("outputs", []):
            name = o.get("name")
            req = o.get("required", False)
            schema = (o.get("schema") or {})
            typ = schema.get("type", "?")
            default = (schema.get("default") or "")[:40]
            ins.append(f"{name}({typ},{'必' if req else '选'},{default})")
        outs = []
        for i in (end or {}).get("data", {}).get("inputs", []):
            outs.append(i.get("name"))
        print(f"=== {f}")
        print("  入参:", " | ".join(ins) if ins else "(无)")
        print("  出参:", outs if outs else "(无)")
        print()

main()
