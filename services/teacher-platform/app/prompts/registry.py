"""Prompt Registry —— B1 渲染提示词的唯一入口（模板归 B2，B1 只经本模块渲染）。

模板文件头为 Jinja2 注释块：
    {#- meta
    purpose: ...
    pipeline: ...
    version: 1.0.0
    vocab: [data/cliche.yaml]      # 依赖词表（相对 app/prompts/）
    evidence: true|false           # 是否消费 evidence.* 段（KNR 六类生成任务必须 true）
    #}

红线（B2 §9.4）：模板输出指令本身不得含 cliche 词表短语——反套话词表只作为检测/约束注入。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined

PROMPTS_DIR = Path(__file__).resolve().parent

_META_PATTERN = re.compile(r"\{#-\s*meta\s*(.*?)#\}", re.S)
_META_FIELD = re.compile(r"^(\w+):\s*(.+)$", re.M)


class PromptNotFoundError(KeyError):
    pass


def _strict_or_lenient(strict: bool) -> type:
    return StrictUndefined if strict else Undefined


class PromptRegistry:
    def __init__(self, *, prompts_dir: Path | None = None, strict: bool = True,
                 release_store=None, fail_closed: bool | None = None) -> None:
        base = prompts_dir or PROMPTS_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(base)),
            undefined=_strict_or_lenient(strict),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,  # 输出给 LLM，不做 HTML 转义
        )
        self._base = base
        # R11.5-B：prompt 发布绑定（approved release/hash/schema/toolset/model policy）
        self._release_store = release_store
        from .release import _default_fail_closed

        self._fail_closed = _default_fail_closed() if fail_closed is None else fail_closed

    def render(self, name: str, /, **ctx: Any) -> str:
        tpl = self._env.get_template(name)
        return tpl.render(**ctx)

    async def render_strict_version(self, name: str, version: str, /, **ctx: Any) -> tuple[str, dict[str, Any] | None]:
        """渲染 + 绑定 approved release；返回 (text, release)。生产 fail-closed 校验 hash/toolset/model policy。"""
        tpl = self._env.get_template(name)
        text = tpl.render(**ctx)
        release = await self._verify_release(name, version, text, ctx)
        return text, release

    async def _verify_release(self, name: str, version: str, rendered: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
        from .release import require_approved
        from app.llm.ai_runs import digest_payload

        content_hash = digest_payload(rendered)
        toolset = ctx.get("_toolset")
        model_policy = ctx.get("_model_policy")
        release = await require_approved(
            self._release_store,
            prompt_name=name,
            version=version,
            content_hash=content_hash,
            fail_closed=self._fail_closed,
            toolset=toolset,
            model_policy=model_policy,
        )
        return release.to_public() if release is not None else None

    def meta(self, name: str) -> dict[str, str | list[str] | bool]:
        src, _filename, _uptodate = self._env.loader.get_source(self._env, name)
        m = _META_PATTERN.search(src)
        if not m:
            return {}
        fields: dict[str, Any] = {}
        for fm in _META_FIELD.finditer(m.group(1)):
            key, raw = fm.group(1), fm.group(2).strip()
            if raw.startswith("["):
                fields[key] = [v.strip() for v in raw.strip("[]").split(",") if v.strip()]
            elif raw in ("true", "false"):
                fields[key] = raw == "true"
            else:
                fields[key] = raw
        return fields

    def assert_evidence_contract(self, generation_templates: list[str]) -> None:
        """KNR 红线自检：六类生成任务模板必须声明 evidence: true。"""
        bad = [n for n in generation_templates if self.meta(n).get("evidence") is not True]
        if bad:
            raise ValueError(f"生成类模板缺少 evidence 段声明: {bad}")

    def load_vocab(self, rel_path: str) -> dict:
        return yaml.safe_load((self._base / rel_path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def default_registry() -> PromptRegistry:
    return PromptRegistry()
