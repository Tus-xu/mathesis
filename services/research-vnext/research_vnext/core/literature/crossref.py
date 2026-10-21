"""RV04 · Crossref typed adapter（真实匿名 REST provider）。"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .provider import (
    AVAIL_NO,
    AVAIL_UNKNOWN,
    AVAIL_YES,
    LiteratureMetadata,
    LiteratureProvider,
    ProviderRateLimited,
    ProviderTimeout,
    hash_raw_response,
    now_iso,
)

CROSSREF_VERSION = "1.0.0"
DEFAULT_BASE = "https://api.crossref.org"
USER_AGENT = "MathArenaResearch/0.4 (mailto:research@example.invalid)"


def _avail(flag: bool | None) -> str:
    if flag is True:
        return AVAIL_YES
    if flag is False:
        return AVAIL_NO
    return AVAIL_UNKNOWN


def _map_authors(items: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for a in items or []:
        out.append(
            {
                "given": a.get("given") or "",
                "family": a.get("family") or "",
                "name": a.get("name") or " ".join(x for x in [a.get("given"), a.get("family")] if x),
                "orcid": a.get("ORCID") or "",
            }
        )
    return out


def _map_work(work: dict[str, Any], *, query: str, request_id: str) -> LiteratureMetadata:
    titles = work.get("title") or []
    title = titles[0] if titles else ""
    doi = (work.get("DOI") or "").lower()
    issued = work.get("issued") or {}
    date_parts = (issued.get("date-parts") or [[None]])[0]
    published = ""
    if date_parts and date_parts[0]:
        parts = [str(p) for p in date_parts if p]
        published = "-".join(parts)

    container = work.get("container-title") or []
    venue = container[0] if container else (work.get("publisher") or "")

    abstract = work.get("abstract") or ""
    # Crossref license 字段
    licenses = work.get("license") or []
    rights_status = "UNKNOWN"
    rights_detail: dict = {"licenses": licenses} if licenses else {}
    if licenses:
        # 有 license URL 也保持 UNKNOWN，除非明确 open
        urls = " ".join((x.get("URL") or "") for x in licenses).lower()
        if "creativecommons" in urls or "open" in urls:
            rights_status = "PUBLIC"
        else:
            rights_status = "RESTRICTED"

    link = work.get("URL") or (f"https://doi.org/{doi}" if doi else "")
    fulltext = AVAIL_UNKNOWN
    links = work.get("link") or []
    # 有 link 不等于有全文可下载；仅当明确 PDF content-type 才标 YES
    if any((x.get("content-type") or "").startswith("application/pdf") for x in links):
        fulltext = AVAIL_YES
    elif links:
        fulltext = AVAIL_UNKNOWN

    raw_hash = hash_raw_response(work)
    return LiteratureMetadata(
        provider="crossref",
        provider_version=CROSSREF_VERSION,
        provider_record_id=doi or work.get("URL") or title[:180],
        request_id=request_id,
        query=query,
        doi=doi,
        title=title,
        authors=_map_authors(work.get("author")),
        published_date=published,
        venue=venue,
        abstract_available=_avail(bool(abstract)) if abstract else AVAIL_NO,
        fulltext_available=fulltext,
        rights_status=rights_status,
        rights_detail=rights_detail,
        source_url=link,
        retrieved_at=now_iso(),
        raw_response_hash=raw_hash,
        raw=work,
    )


class CrossrefProvider(LiteratureProvider):
    name = "crossref"
    version = CROSSREF_VERSION

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ):
        self.base_url = (base_url or os.getenv("RESEARCH_CROSSREF_BASE", DEFAULT_BASE)).rstrip("/")
        self.timeout = float(timeout if timeout is not None else float(os.getenv("RESEARCH_CROSSREF_TIMEOUT", "8")))
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def _handle_error(self, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After")
            raise ProviderRateLimited(
                "Crossref 限流",
                retry_after=int(ra) if ra and ra.isdigit() else None,
            )
        if resp.status_code >= 500:
            from .provider import LiteratureProviderError

            raise LiteratureProviderError(
                "PROVIDER_ERROR",
                f"Crossref HTTP {resp.status_code}",
                retryable=True,
                detail={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            from .provider import LiteratureProviderError

            raise LiteratureProviderError(
                "PROVIDER_ERROR",
                f"Crossref HTTP {resp.status_code}",
                retryable=False,
                detail={"status": resp.status_code},
            )

    def search(self, query: str, *, limit: int = 5, request_id: str = "") -> list[LiteratureMetadata]:
        owned = self._client is None
        client = self._http()
        try:
            try:
                resp = client.get(
                    "/works",
                    params={"query.bibliographic": query, "rows": max(1, min(limit, 20))},
                )
            except httpx.TimeoutException as e:
                raise ProviderTimeout(f"Crossref 超时: {e}") from e
            except httpx.HTTPError as e:
                from .provider import LiteratureProviderError

                raise LiteratureProviderError("NETWORK_ERROR", str(e), retryable=True) from e
            self._handle_error(resp)
            data = resp.json()
            items = (((data or {}).get("message") or {}).get("items")) or []
            return [_map_work(w, query=query, request_id=request_id) for w in items[:limit]]
        finally:
            if owned:
                client.close()

    def fetch_by_doi(self, doi: str, *, request_id: str = "") -> LiteratureMetadata | None:
        doi = (doi or "").strip().lower().removeprefix("https://doi.org/").removeprefix("doi:")
        if not doi:
            return None
        owned = self._client is None
        client = self._http()
        try:
            try:
                resp = client.get(f"/works/{doi}")
            except httpx.TimeoutException as e:
                raise ProviderTimeout(f"Crossref 超时: {e}") from e
            if resp.status_code == 404:
                return None
            self._handle_error(resp)
            work = (resp.json() or {}).get("message") or {}
            return _map_work(work, query=f"doi:{doi}", request_id=request_id)
        finally:
            if owned:
                client.close()



