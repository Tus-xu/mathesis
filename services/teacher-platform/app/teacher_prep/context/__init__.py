"""R16 TextbookTeachingContext：不可变教材教学上下文（8100 业务实体）。

命名空间约定（DECISIONS.md §2/§3）：
- 本包产出 8100 拥有的 TextbookTeachingContext 业务快照：由教师确认的
  教材版本/章节/知识点/课时/班级 确定性构建，零 LLM、零编造；
- 教材事实全部来自 direct-chunk 切片（Source→Section→Chunk→Unit/Problem），
  每条事实携带可解析 SourceLocator；覆盖不足/证据冲突/章节不完整显式告警；
- 所有 chunk/图片/用户输入按 UNTRUSTED DATA 处理（安全原语见 safety.py）。
"""

from __future__ import annotations

from .builder import build_teaching_context
from .models import (
    ContextCoverage,
    ContextFact,
    ContextSelection,
    ContextWarning,
    TextbookTeachingContext,
    canonical_snapshot_hash,
)
from .safety import (
    detect_injection,
    is_bad_chunk,
    neutralize_untrusted,
)

__all__ = [
    "build_teaching_context",
    "ContextCoverage",
    "ContextFact",
    "ContextSelection",
    "ContextWarning",
    "TextbookTeachingContext",
    "canonical_snapshot_hash",
    "detect_injection",
    "is_bad_chunk",
    "neutralize_untrusted",
]
