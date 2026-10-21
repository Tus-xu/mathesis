"""RV13 · 输入清洗：文本 / 富文本 HTML / URL scheme allowlist。

为什么放在**存储域**而不是只放前端：Tiptap/DOMPurify 管住浏览器渲染端，
但任何绕过前端的写入路径（API 直调、AI patch、未来导入器）都必须被
服务端等价地拦住 —— 信任边界在服务端，不在编辑器。

规则（任务书第 2 条）：
- 文本字段：canonical 模型存**纯文本**（所有标签剥离，实体解码）；
- 富文本：allowlist 标签白名单，`script/style/iframe/object/embed/link/meta`
  连同内容整体移除；全部 `on*` 事件属性剥除；
- URL：scheme 封闭 allowlist `{http, https, mailto}`，其余一律拒绝
  （`javascript:` / `data:` / `vbscript:` / `file:` 均不允许）。
"""

from __future__ import annotations

import html as _html
from html.parser import HTMLParser

from .errors import PaperSchemaError

#: URL scheme 封闭 allowlist
URL_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https", "mailto")

#: 富文本 allowlist（结构标签；无 script/style/iframe/对象嵌入）
RICH_TEXT_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p", "br", "strong", "em", "b", "i", "u", "code", "sub", "sup",
        "ul", "ol", "li", "blockquote", "span", "a", "h3", "h4", "h5", "h6",
    }
)

#: 自闭合标签（无法携带文本内容）
_VOID_TAGS = {"br"}

#: URL 属性（值必须过 scheme allowlist）
_URL_ATTRS = {"href", "src"}

#: 全局剥除的危险属性（事件处理器等）
_FORBIDDEN_ATTR_PREFIX = "on"
_FORBIDDEN_ATTRS = {"style", "formaction", "xlink:href", "srcset"}

_MAX_TEXT = 20000
_MAX_URL = 2048


def sanitize_text(text: str, *, limit: int = _MAX_TEXT) -> str:
    """canonical 文本字段清洗：剥全部标签、解实体、压长度。

    中文与数学符号（≤ ≥ ± × 等）原样保留 —— 只动"结构"，不动"内容"。
    """
    if not text:
        return ""
    no_tags = _strip_all_tags(str(text))
    decoded = _html.unescape(no_tags)
    # 解码后再剥一次，拦截 `&lt;script&gt;` 的二次注入
    decoded = _strip_all_tags(decoded)
    decoded = decoded.replace("\x00", "").strip()
    if len(decoded) > limit:
        return decoded[:limit]
    return decoded


def _strip_all_tags(text: str) -> str:
    class _Dropper(HTMLParser):
        """剥全部标签，且 script/style 的**内容**一并丢弃（不是只丢标签）。"""

        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.parts: list[str] = []
            self.skip_depth = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: D102
            if tag.lower() in {"script", "style"}:
                self.skip_depth += 1

        def handle_endtag(self, tag: str) -> None:  # noqa: D102
            if tag.lower() in {"script", "style"} and self.skip_depth:
                self.skip_depth -= 1

        def handle_data(self, data: str) -> None:  # noqa: D102
            if not self.skip_depth:
                self.parts.append(data)

    p = _Dropper()
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001 - 解析失败时宁可保留纯文本也不抛给用户
        return text
    return "".join(p.parts)


def sanitize_rich_html(html: str) -> tuple[str, list[str]]:
    """富文本 allowlist 清洗。返回 `(clean, removed_report)`。

    removed_report 是**人可读**的移除清单（前端可展示"输入里哪些东西被丢掉了"），
    不做静默丢弃。
    """
    removed: list[str] = []

    class _Filter(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.out: list[str] = []
            self.skip_depth = 0
            self.skip_tag = ""

        def _attrs(self, attrs: list[tuple[str, str | None]], tag: str) -> str:
            keep: list[str] = []
            for name, value in attrs:
                lname = (name or "").lower()
                if lname.startswith(_FORBIDDEN_ATTR_PREFIX) or lname in _FORBIDDEN_ATTRS:
                    removed.append(f"{tag}@{lname}")
                    continue
                if lname in _URL_ATTRS and value:
                    try:
                        sanitize_url(value)
                    except PaperSchemaError:
                        removed.append(f"{tag}@{lname}({(value or '')[:32]}…)")
                        continue
                keep.append(f'{lname}="{_html.escape(value or "", quote=True)}"')
            return (" " + " ".join(keep)) if keep else ""

        # script/style/iframe 等连同**内容**一起丢弃
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: D102
            t = tag.lower()
            if t in {"script", "style", "iframe", "object", "embed", "noscript"}:
                if self.skip_depth == 0:
                    self.skip_tag = t
                    removed.append(t)
                self.skip_depth += 1
                return
            if self.skip_depth:
                return
            if t in RICH_TEXT_ALLOWED_TAGS:
                if t in _VOID_TAGS:
                    self.out.append(f"<{t}>")
                else:
                    self.out.append(f"<{t}{self._attrs(attrs, t)}>")
            else:
                removed.append(f"tag:{t}")

        def handle_endtag(self, tag: str) -> None:  # noqa: D102
            t = tag.lower()
            if self.skip_depth:
                if t == self.skip_tag:
                    self.skip_depth -= 1
                    if self.skip_depth == 0:
                        self.skip_tag = ""
                return
            if t in RICH_TEXT_ALLOWED_TAGS and t not in _VOID_TAGS:
                self.out.append(f"</{t}>")

        def handle_data(self, data: str) -> None:  # noqa: D102
            if self.skip_depth:
                return
            self.out.append(_html.escape(data, quote=False))

        def handle_entityref(self, name: str) -> None:  # noqa: D102
            if not self.skip_depth:
                self.out.append(f"&{name};")

    f = _Filter()
    f.feed(str(html or ""))
    f.close()
    return "".join(f.out), removed


def sanitize_url(url: str) -> str:
    """URL scheme allowlist。不允许的 scheme 抛 `PAPER_URL_SCHEME_FORBIDDEN`。"""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if len(raw) > _MAX_URL:
        raise PaperSchemaError("PAPER_URL_TOO_LONG", "URL 超长")
    # scheme: `^[a-zA-Z][a-zA-Z0-9+.-]*:`；无 scheme 的相对 URL 一律拒绝
    # （论文中的资源引用必须可回源，相对路径无回源语义）。
    scheme_end = raw.find(":")
    if scheme_end <= 0:
        raise PaperSchemaError("PAPER_URL_SCHEME_FORBIDDEN", f"URL 缺少允许的 scheme: {raw[:64]}")
    scheme = raw[:scheme_end].lower()
    # 拦截 `\r\n` / 控制字符与空白混入 scheme 的绕过
    if not scheme.isalnum() or scheme not in URL_ALLOWED_SCHEMES:
        raise PaperSchemaError(
            "PAPER_URL_SCHEME_FORBIDDEN",
            f"URL scheme {scheme!r} 不在 allowlist {list(URL_ALLOWED_SCHEMES)}",
        )
    if any(c.isspace() for c in raw[:scheme_end + 2]):
        raise PaperSchemaError("PAPER_URL_SCHEME_FORBIDDEN", "scheme 含非法字符")
    return raw
