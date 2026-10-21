"""RV07 · 证据域（EvidenceCard / Citation）。

分层：
- `types` —— 七类证据 + 支持强度矩阵（语义唯一轴）；
- `contracts` —— `EvidenceCard`（事实）/ `EvidenceProposal`（AI 只能产出这个）；
- `rights` —— 权利 → 出口（quote / reader / export / prompt / log）；
- `locator` —— 回源（RV05 anchor 或可验证外部定位）+ stale；
- `no_evidence` —— UNKNOWN / UNSUPPORTED 拒答语义；
- `service` / `models` / `routes` —— 落库、校验、HTTP。

本包**不**实现 Claim 真假裁决、不生成 Paper、不编译 —— 那是 RV12/RV13 的职责。
"""

from __future__ import annotations

__all__ = ["types", "contracts", "rights", "locator", "no_evidence"]
