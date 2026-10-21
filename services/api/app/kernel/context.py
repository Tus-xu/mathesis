"""上下文装配（kernel/context.py）

总预算 12K token，P0~P2 保命段永不裁。
裁剪顺序：P3→P5→P4→P6（ADR-001-10）。
"""

import re
from pathlib import Path

import structlog

from app.kernel.memory import UserProfileData, WorkingMemory

logger = structlog.get_logger()

# Persona 提示词目录
_PROMPTS_DIR = Path(__file__).parent / "prompts"

# 中英文混合 token 估算常量
_CN_CHAR_PER_TOKEN = 1.5
_EN_CHAR_PER_TOKEN = 4.0
_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def _estimate_tokens(text: str) -> int:
    """中英文混合 token 估算。

    中文约 1.5 字符/token，英文约 4 字符/token。
    比原先统一 len(text)/1.5 更精确。
    """
    if not text:
        return 0
    cn_chars = len(_CN_CHAR_RE.findall(text))
    en_chars = len(text) - cn_chars
    return int(cn_chars / _CN_CHAR_PER_TOKEN + en_chars / _EN_CHAR_PER_TOKEN) + 1


# Persona 文本缓存（启动后不变，避免每请求读盘）
_persona_cache: dict[str, str] = {}


def _load_persona(role: str) -> str:
    """加载角色 Persona 提示词（带进程内缓存）"""
    if role in _persona_cache:
        return _persona_cache[role]

    text = ""
    persona_file = _PROMPTS_DIR / f"{role}.md"
    if persona_file.exists():
        text = persona_file.read_text(encoding="utf-8")
    else:
        # 默认回退到 student
        default_file = _PROMPTS_DIR / "student.md"
        if default_file.exists():
            text = default_file.read_text(encoding="utf-8")
        else:
            text = "你是一位数学助手。数学公式使用 LaTeX 格式：行内用 \\(...\\)，独立公式用 $$...$$，分步推理并给出依据。"

    _persona_cache[role] = text
    return text


class ContextAssembler:
    """上下文装配器：12K 预算，P0~P7 分段"""

    BUDGET = {
        "P0_system_persona": 800,
        "P0_learning_profile": 800,  # v1.2：学情画像卡（含近期错题明细，AI 全局知晓学生）
        "P1_user_message": 2000,
        "P2_skill_params": 600,
        "P3_rag_chunks": 4000,
        "P4_working_memory": 1600,
        "P5_user_profile": 500,
        "P6_episodic": 800,
        "P7_output_spec": 400,
        "P9_platform_map": 500,  # v1.2：平台地图（AI 全局知晓平台功能）
    }

    TOTAL_BUDGET = 13_000  # 总预算 13K tokens（含 P0 画像卡 + P9 平台地图增量）

    # P4 最近消息最少保留数
    _MIN_RECENT_MESSAGES = 3
    # P4 摘要最短保留字符数
    _MIN_SUMMARY_CHARS = 50

    async def assemble(
        self,
        *,
        user_message: str,
        active_role: str = "student",
        working_memory: WorkingMemory | None = None,
        user_profile: UserProfileData | None = None,
        rag_chunks: list[dict] | None = None,
        skill_params: dict | None = None,
        output_spec: str = "",
        episodic_memories: list[dict] | None = None,
        learning_profile_text: str = "",  # v1.2：P0 学情画像卡（AI 全局知晓学生）
        platform_map_text: str = "",  # v1.2：P9 平台地图（AI 全局知晓平台功能）
    ) -> list[dict]:
        """装配上下文消息列表。

        按 P0~P9 预算分配，P0~P2 保命段永不裁。
        裁剪顺序：P3→P5→P4→P6。
        """
        # ===== 构建各层级内容 =====

        # P0: System Persona（保命段，永不裁）
        persona = _load_persona(active_role)
        system_core = persona

        # P0b: 学情画像卡（v1.2：注入 system，让模型知晓学生学情）
        # 超长时截断（调用方已按 token 预算生成；此处兜底）
        if learning_profile_text and _estimate_tokens(learning_profile_text) > self.BUDGET[
            "P0_learning_profile"
        ]:
            learning_profile_text = (
                learning_profile_text[
                    : int(self.BUDGET["P0_learning_profile"] * _CN_CHAR_PER_TOKEN)
                ]
                + "\n（画像已截断）"
            )

        # P9: 平台地图（v1.2：注入 system，让模型知晓平台功能可直达）
        if platform_map_text and _estimate_tokens(platform_map_text) > self.BUDGET["P9_platform_map"]:
            platform_map_text = (
                platform_map_text[
                    : int(self.BUDGET["P9_platform_map"] * _CN_CHAR_PER_TOKEN)
                ]
                + "\n（功能列表已截断）"
            )

        # P7: Output Spec（附加到 system core）
        if output_spec:
            system_core += f"\n\n## 输出要求\n{output_spec}"

        # P5: User Profile（可裁剪）
        profile_text = self._build_profile_text(user_profile)

        # P4 summary: 对话摘要（可裁剪）
        summary_text = ""
        if working_memory and working_memory.summary:
            summary_text = working_memory.summary

        # P3: RAG Chunks（可裁剪，最高优先裁剪）
        rag_text = ""
        if rag_chunks:
            rag_text = self._format_rag_chunks(rag_chunks)

        # P4 recent_messages（可裁剪）
        recent_messages: list[dict] = []
        if working_memory and working_memory.recent_messages:
            recent_messages = list(working_memory.recent_messages)

        # P6: Episodic Memory（可裁剪，最低优先）
        # 数据来源：显式 episodic_memories 参数优先；否则消费 working_memory 中
        # MemoryManager 预检索的长期记忆（chat 主链路唯一激活路径，skills 层零改动）
        episodic_text = ""
        memories = episodic_memories
        if memories is None and working_memory is not None:
            memories = working_memory.episodic_memories
        if memories:
            episodic_text = self._format_episodic_memories(memories)

        # ===== 计算总 token =====
        all_parts = {
            "system_core": system_core,
            "learning_profile_text": learning_profile_text,
            "platform_map_text": platform_map_text,
            "profile_text": profile_text,
            "summary_text": summary_text,
            "rag_text": rag_text,
            "recent_messages": recent_messages,
            "episodic_text": episodic_text,
            "user_message": user_message,
        }
        total_tokens = self._estimate_all_tokens(all_parts)

        logger.info(
            "context.assembled",
            total_messages=len(recent_messages) + 3,  # system + rag + user + recent
            estimated_tokens=total_tokens,
            has_rag=bool(rag_chunks),
            has_memory=bool(working_memory and working_memory.summary),
            has_learning_profile=bool(learning_profile_text),
            has_platform_map=bool(platform_map_text),
            role=active_role,
        )

        # ===== 超预算时按 P3→P5→P4→P6 顺序裁剪 =====
        trim_log: list[str] = []
        if total_tokens > self.TOTAL_BUDGET:
            rag_text, profile_text, summary_text, recent_messages, episodic_text, trim_log = (
                self._trim_to_budget(
                    system_core=system_core,
                    user_message=user_message,
                    rag_text=rag_text,
                    profile_text=profile_text,
                    summary_text=summary_text,
                    recent_messages=recent_messages,
                    episodic_text=episodic_text,
                    total_tokens=total_tokens,
                )
            )

        # ===== 合并为最终 messages 列表 =====
        messages: list[dict] = []

        # system = P0 + P0b(画像卡) + P7 + P5(裁剪后) + P4-summary(裁剪后) + P6(裁剪后) + P9(平台地图)
        system_content = system_core
        if learning_profile_text:
            system_content += f"\n\n{learning_profile_text}"
        if profile_text:
            system_content += f"\n\n## 学生档案\n{profile_text}"
        if summary_text:
            system_content += f"\n\n## 对话摘要（之前的讨论）\n{summary_text}"
        if episodic_text:
            system_content += f"\n\n{episodic_text}"
        if platform_map_text:
            system_content += f"\n\n{platform_map_text}"
        messages.append({"role": "system", "content": system_content})

        # P3: RAG 独立 system 消息
        if rag_text:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"## 参考资料（来自教材知识库）\n{rag_text}\n\n"
                        "请基于以上资料回答，引用处标注【N】（N为资料编号）。"
                        "如果资料不足以回答，请说明。"
                    ),
                }
            )

        # P4: 最近消息
        for msg in recent_messages:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # P1: User Message（保命段，永不裁）
        messages.append({"role": "user", "content": user_message})

        if trim_log:
            logger.info("context.trim_applied", steps=trim_log)

        return messages

    # ------------------------------------------------------------------ #
    #  内部辅助
    # ------------------------------------------------------------------ #

    def _build_profile_text(self, user_profile: UserProfileData | None) -> str:
        """构建 P5 用户档案文本（不含 section header）。"""
        if not user_profile:
            return ""
        parts: list[str] = []
        if user_profile.grade:
            parts.append(f"- 年级：{user_profile.grade}")
        if user_profile.level != "unknown":
            parts.append(f"- 数学水平：{user_profile.level}")
        if user_profile.weak_points:
            # weak_points 兼容两种存储形态：str（kp_code，见 student_router._update_weak_points）
            # 或 dict（{"name": ...}）；历史数据两种都存在，这里统一容错
            weak = ", ".join(
                wp.get("name", "") if isinstance(wp, dict) else str(wp)
                for wp in user_profile.weak_points
            )
            parts.append(f"- 薄弱点：{weak}")
        if user_profile.preferences:
            prefs = ", ".join(f"{k}={v}" for k, v in user_profile.preferences.items())
            parts.append(f"- 偏好：{prefs}")
        return "\n".join(parts)

    def _format_rag_chunks(self, chunks: list[dict]) -> str:
        """格式化 RAG 切片为引用文本"""
        parts = []
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("doc_title", "教材")
            content = chunk.get("content", "")
            parts.append(f"【{i}】（来源：{source}）\n{content}")
        return "\n\n".join(parts)

    # P6 学生长期记忆注入预算（token）：超出后从尾部（最低优先/最旧）逐条丢弃
    _P6_EPISODIC_TOKEN_BUDGET = 200

    # kind → 中文行前缀
    _EPISODIC_KIND_LABELS = {
        "weak_kp": "常错",
        "preference": "偏好",
        "goal": "目标",
        "note": "备注",
    }

    def _format_episodic_memories(self, memories: list[dict]) -> str:
        """格式化 P6 学生长期记忆，形如：
        【学生长期记忆】
        - 常错：三角恒等变换（2026-08-01）

        总预算 ≤200 token，超出时从尾部条目开始丢弃（调用方已按相关度/优先级
        排序，尾部 = 最低优先）；首条至少保留一行，避免出现裸标题。
        """
        header = "【学生长期记忆】"
        kept: list[str] = []
        used = _estimate_tokens(header)
        for mem in memories:
            content = str(mem.get("content") or "").strip()
            if not content:
                continue
            label = self._EPISODIC_KIND_LABELS.get(str(mem.get("kind") or "note"), "备注")
            line = f"- {label}：{content}"
            date_text = self._episodic_date_text(mem.get("created_at"))
            if date_text:
                line += f"（{date_text}）"
            cost = _estimate_tokens(line)
            if kept and used + cost > self._P6_EPISODIC_TOKEN_BUDGET:
                break
            kept.append(line)
            used += cost
        if not kept:
            return ""
        return header + "\n" + "\n".join(kept)

    @staticmethod
    def _episodic_date_text(created_at) -> str:
        """记忆时间格式化为 YYYY-MM-DD（兼容 datetime 与 ISO 字符串）"""
        if created_at is None:
            return ""
        if hasattr(created_at, "strftime"):
            return created_at.strftime("%Y-%m-%d")
        return str(created_at)[:10]

    def _estimate_all_tokens(self, parts: dict) -> int:
        """估算所有层级的总 token。"""
        total = 0
        for _key, val in parts.items():
            if isinstance(val, str):
                total += _estimate_tokens(val)
            elif isinstance(val, list):
                for msg in val:
                    total += _estimate_tokens(msg.get("content", ""))
        return total

    def _trim_to_budget(
        self,
        *,
        system_core: str,
        user_message: str,
        rag_text: str,
        profile_text: str,
        summary_text: str,
        recent_messages: list[dict],
        episodic_text: str,
        total_tokens: int,
    ) -> tuple[str, str, str, list[dict], str, list[str]]:
        """按 P3→P5→P4→P6 顺序裁剪到预算内。

        返回裁剪后的各层内容及裁剪日志。
        P0(system_core)/P1(user_message) 永不裁剪。
        """
        budget = self.TOTAL_BUDGET
        trim_log: list[str] = []

        def _current_tokens() -> int:
            return (
                _estimate_tokens(system_core)
                + _estimate_tokens(user_message)
                + _estimate_tokens(rag_text)
                + _estimate_tokens(profile_text)
                + _estimate_tokens(summary_text)
                + sum(_estimate_tokens(m.get("content", "")) for m in recent_messages)
                + _estimate_tokens(episodic_text)
            )

        # ---- Step 1: 裁剪 P3 RAG（最高优先） ----
        if _current_tokens() > budget and rag_text:
            original_len = len(rag_text)
            # 逐步缩减 RAG：按行截断
            max_rag_chars = int(self.BUDGET["P3_rag_chunks"] * _CN_CHAR_PER_TOKEN)
            if len(rag_text) > max_rag_chars:
                rag_text = rag_text[:max_rag_chars] + "\n...（资料已截断）"
            trim_log.append(f"P3_rag: {original_len}→{len(rag_text)} chars")
            logger.info("context.trim_p3", before=original_len, after=len(rag_text))

        # ---- Step 2: 裁剪 P5 User Profile ----
        if _current_tokens() > budget and profile_text:
            original_len = len(profile_text)
            # 截断薄弱点列表：只保留前 2 项
            lines = profile_text.split("\n")
            new_lines = []
            for line in lines:
                if line.startswith("- 薄弱点："):
                    # 只保留前 2 个薄弱点
                    items = line.replace("- 薄弱点：", "").split(", ")
                    if len(items) > 2:
                        line = "- 薄弱点：" + ", ".join(items[:2])
                elif line.startswith("- 偏好："):
                    # 截断偏好
                    items = line.replace("- 偏好：", "").split(", ")
                    if len(items) > 2:
                        line = "- 偏好：" + ", ".join(items[:2])
                new_lines.append(line)
            profile_text = "\n".join(new_lines)
            # 如果仍然太长，进一步截断
            max_profile_chars = int(self.BUDGET["P5_user_profile"] * _CN_CHAR_PER_TOKEN)
            if len(profile_text) > max_profile_chars:
                profile_text = profile_text[:max_profile_chars] + "..."
            trim_log.append(f"P5_profile: {original_len}→{len(profile_text)} chars")
            logger.info("context.trim_p5", before=original_len, after=len(profile_text))

        # ---- Step 3: 裁剪 P4 Working Memory ----
        if _current_tokens() > budget:
            # 3a: 减少最近消息数量（保留最近 _MIN_RECENT_MESSAGES 条）
            while len(recent_messages) > self._MIN_RECENT_MESSAGES and _current_tokens() > budget:
                removed = recent_messages.pop(0)
                trim_log.append(
                    f"P4_recent_msg_removed: role={removed.get('role')}, "
                    f"len={len(removed.get('content', ''))}"
                )
            logger.info("context.trim_p4_messages", remaining=len(recent_messages))

            # 3b: 压缩摘要
            if _current_tokens() > budget and summary_text:
                original_len = len(summary_text)
                max_summary_chars = int(self.BUDGET["P4_working_memory"] * _CN_CHAR_PER_TOKEN * 0.5)
                max_summary_chars = max(max_summary_chars, self._MIN_SUMMARY_CHARS)
                if len(summary_text) > max_summary_chars:
                    summary_text = summary_text[:max_summary_chars] + "...（摘要已压缩）"
                    trim_log.append(f"P4_summary: {original_len}→{len(summary_text)} chars")
                    logger.info(
                        "context.trim_p4_summary",
                        before=original_len,
                        after=len(summary_text),
                    )

        # ---- Step 4: 裁剪 P6 Episodic Memory（最低优先） ----
        if _current_tokens() > budget and episodic_text:
            original_len = len(episodic_text)
            # 逐条丢弃尾部记忆条目（相关度/优先级最低），首行【学生长期记忆】标题保留；
            # 条目丢光仍超预算则整段清空（P6 为最低优先段，可整段舍弃）
            lines = episodic_text.split("\n")
            header, entries = lines[0], lines[1:]
            while entries and _current_tokens() > budget:
                entries.pop()
            episodic_text = (header + "\n" + "\n".join(entries)) if entries else ""
            trim_log.append(f"P6_episodic: {original_len}→{len(episodic_text)} chars")
            logger.info("context.trim_p6", before=original_len, after=len(episodic_text))

        # ---- 最终检查 ----
        final_tokens = _current_tokens()
        if final_tokens > budget:
            logger.warning(
                "context.trim_insufficient",
                final_tokens=final_tokens,
                budget=budget,
                overflow=final_tokens - budget,
            )

        return rag_text, profile_text, summary_text, recent_messages, episodic_text, trim_log


# ---- 全局单例 ----
_context_assembler: ContextAssembler | None = None


def get_context_assembler() -> ContextAssembler:
    """获取全局 ContextAssembler 单例"""
    global _context_assembler
    if _context_assembler is None:
        _context_assembler = ContextAssembler()
    return _context_assembler
