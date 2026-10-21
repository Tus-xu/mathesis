"""RV09 · 生成 `CONTEXT-MANIFEST-SAMPLE.json`。

**为什么不手写这份样例**：手写的样例只能证明"文档写得好看"，
不能证明 `build_manifest` 真的会这样裁剪。因此本脚本**直接调用生产代码**，
用一组刻意构造的输入（超预算会话 + 未授权偏好 + 命名空间不符偏好 +
失效证据 + 注入信号）跑出清单，再把结果原样落盘。

生成物同时充当两张契约的样本：
1. `research_ai_run.context_manifest` 的落库形状（**不含正文**）；
2. 渲染出的提示词正文与清单的**一一对应**关系（脚本会断言这一点）。

用法：
    python scripts/rv09_manifest_sample.py --out <RV09 目录>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.llm import context_manifest as CM  # noqa: E402

NS = {
    "tenantId": "11111111-1111-1111-1111-111111111111",
    "userId": "22222222-2222-2222-2222-222222222222",
    "researchId": "33333333-3333-3333-3333-333333333333",
    "purpose": "ASSISTANT_STYLE",
}
NS_KEY = "\x1f".join([NS["tenantId"], NS["userId"], NS["researchId"], NS["purpose"]])


def build() -> dict:
    conversation = []
    for i in range(12):
        conversation.append({"id": f"u{i}", "ref": f"message:{i * 2}", "role": "user", "text": f"第 {i + 1} 轮追问：" + "关于排队模型的边界条件，" * 6})
        conversation.append({"id": f"a{i}", "ref": f"message:{i * 2 + 1}", "role": "assistant", "text": f"第 {i + 1} 轮回答：" + "先给结论再给推导，" * 6})

    text, manifest = CM.build_manifest(
        manifest_id="ctx:demo-session:00000000",
        namespace=NS,
        request_text="请把已确认的问题地图里的成功判据收紧到平均等待时间不超过 2 分钟。",
        conversation=conversation,
        session_summary={
            "ref": "summary:v3",
            "generatedBy": "deterministic-domain-projection",
            "content": {"title": "校园快递点排队优化", "revision": 17, "artifact_count": 3, "event_count": 42},
        },
        domain_state={
            "ref": "domain:session-objects",
            "objects": {"problemMap": {"exists": True, "revisions": 1, "latest": 1}},
            "problemMap": {"revision": 1, "currentState": "如何为校园快递点设计取件排队方案（未闭合段：risks）"},
            "researchMap": {"revision": None, "perspectives": ""},
        },
        preferences=[
            {
                "ref": "preference:length",
                "key": "length",
                "value": "回答尽量简短",
                "authorized": True,
                "namespace": NS_KEY,
                "source": "user",
            },
            {
                "ref": "preference:guessed-notation",
                "key": "notation",
                "value": "（模型猜的记号偏好）",
                "authorized": False,
                "source": "user",
            },
            {
                "ref": "preference:other-project",
                "key": "method",
                "value": "（另一个研究项目下的偏好）",
                "authorized": True,
                "namespace": NS_KEY.replace(NS["researchId"], "99999999-9999-9999-9999-999999999999"),
                "source": "user",
            },
        ],
        evidence=[
            {"ref": "evidence:active-1", "label": "排队论教材 §4.2", "snippet": "M/M/c 平均等待时间闭式解……", "authorized": True, "source": "research"},
            {"ref": "evidence:invalidated", "label": "已撤权来源", "snippet": "……", "authorized": True, "invalidated": True, "source": "research"},
            {"ref": "evidence:stale", "label": "版本已过期", "snippet": "……", "authorized": True, "stale_state": "STALE", "source": "research"},
            {"ref": "evidence:unauthorized", "label": "未授权来源", "snippet": "……", "authorized": False, "source": "research"},
        ],
        injection_signals=["INSTRUCTION_OVERRIDE", "DELIMITER_SPOOF"],
        budget=1_200,
    )

    # 断言 1：清单声明包含的段，必须真的出现在渲染正文里；被排除的必须不出现。
    for s in manifest.sections:
        assert f"ref={s.ref}" in text, f"清单声明包含 {s.ref}，但渲染结果里没有"
    for s in manifest.excluded:
        assert f"ref={s.ref}" not in text, f"{s.ref} 已被排除，却出现在渲染结果里"

    record = manifest.to_record()
    # 断言 2：落库形状**不得**出现正文。
    blob = json.dumps(record, ensure_ascii=False)
    for secret in ("关于排队模型的边界条件", "先给结论再给推导", "回答尽量简短"):
        assert secret not in blob, f"落库形状里出现了正文片段：{secret}"

    return {
        "generatedBy": "scripts/rv09_manifest_sample.py（直接调用生产代码，不是手写样例）",
        "inputs": {
            "budgetChars": 1_200,
            "conversationTurns": 12,
            "preferences": ["已授权/同命名空间", "未授权", "其它研究项目"],
            "evidence": ["ACTIVE", "INVALIDATED", "STALE", "UNAUTHORIZED"],
            "injectionSignals": ["INSTRUCTION_OVERRIDE", "DELIMITER_SPOOF"],
        },
        "record": record,
        "renderedPromptExcerpt": text[:1200] + ("\n…（已截断，实际渲染长度 "
                                                f"{len(text)} 字符）" if len(text) > 1200 else ""),
        "observations": {
            "sectionsIncluded": [s.as_dict() for s in manifest.sections],
            "sectionsExcluded": [s.as_dict() for s in manifest.excluded],
            "truncated": manifest.truncated,
            "usedChars": manifest.used,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="RV09 输出目录")
    args = parser.parse_args()
    payload = build()
    path = Path(args.out) / "CONTEXT-MANIFEST-SAMPLE.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written -> {path}")
    print(json.dumps(payload["observations"], ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
