"""Adapter between MOSAIC Paper objects and KnowledgeSeek PaperRecord.

MOSAIC stays an optional dependency: importing this module never imports
mosaic. Only data mapping and provider-result shaping live here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from paperseek_core.sources.providers import (
    PaperAuthor,
    PaperCitation,
    PaperIdentifiers,
    PaperLinks,
    PaperNames,
    PaperRecord,
    PaperSource,
    ProviderSearchResult,
    SearchMetadata,
    normalize_doi,
)

PROVIDER_ID = "federated"


class MosaicNotInstalledError(RuntimeError):
    """Raised when the optional mosaic package is required but missing."""


def mosaic_available() -> bool:
    """Return True when the optional MOSAIC federated modules can be imported."""
    try:
        import mosaic.models  # noqa: F401
        import mosaic.services  # noqa: F401
        import mosaic.source_registry  # noqa: F401
    except Exception:
        return False
    return True


def require_mosaic():
    """Import the MOSAIC package and modules used by federated retrieval."""
    try:
        import mosaic
        import mosaic.models  # noqa: F401
        import mosaic.services  # noqa: F401
        import mosaic.source_registry  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on environment
        raise MosaicNotInstalledError(
            "The 'mosaic' package is required for federated retrieval. "
            "Install it with: pip install 'paperseek[federated]'"
        ) from exc
    return mosaic


def mosaic_paper_from_dict(payload: Dict[str, Any]):
    mosaic = require_mosaic()
    return mosaic.models.Paper.from_dict(payload)


def paper_to_record(
    paper: Any,
    limit_hint: int = 0,
    provenance_sources: Optional[List[str]] = None,
) -> PaperRecord:
    """Convert a MOSAIC Paper into a KnowledgeSeek PaperRecord."""
    get = lambda key: getattr(paper, key, None)

    title = (get("title") or "").strip()
    doi = normalize_doi(get("doi") or "")
    arxiv_id = (get("arxiv_id") or "").strip()
    openalex_id = (get("openalex_id") or "").strip()
    url = get("url") or ""
    pdf_url = get("pdf_url") or ""

    authors = []
    for name in get("authors") or []:
        display = (name or "").strip()
        if display:
            authors.append(PaperAuthor(display_name=display, wos_standard=display))

    citation_count = get("citation_count")
    citation_count = int(citation_count) if citation_count is not None else 0
    mosaic_source = (get("source") or "").strip()

    identifiers = PaperIdentifiers(
        doi=doi,
        arxiv=arxiv_id,
        openalex=openalex_id,
    )

    raw_payload: Dict[str, Any] = {}
    to_dict = getattr(paper, "to_dict", None)
    if callable(to_dict):
        try:
            raw_payload = to_dict() or {}
        except Exception:
            raw_payload = {}
    if mosaic_source:
        raw_payload = {**raw_payload, "mosaic_source": mosaic_source}
    if provenance_sources:
        raw_payload = {
            **raw_payload,
            "mosaic_sources": list(dict.fromkeys(str(value) for value in provenance_sources if value)),
        }

    return PaperRecord(
        uid=doi or arxiv_id or openalex_id or title,
        title=title,
        source=PaperSource(
            source_title=(get("journal") or "").strip(),
            publish_year=get("year"),
        ),
        names=PaperNames(authors=authors),
        links=PaperLinks(
            record=url,
            landing_page=url,
            pdf=pdf_url,
        ),
        citations=[PaperCitation(db=mosaic_source or "mosaic", count=citation_count)] if citation_count else [],
        identifiers=identifiers,
        abstract=(get("abstract") or "") or "",
        provider=PROVIDER_ID,
        raw=raw_payload,
    )


def _paper_uid(paper: Any) -> str:
    uid = getattr(paper, "uid", "")
    if uid:
        return str(uid)
    doi = normalize_doi(getattr(paper, "doi", "") or "")
    if doi:
        return f"doi:{doi.lower()}"
    arxiv_id = (getattr(paper, "arxiv_id", "") or "").strip()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    pii = (getattr(paper, "pii", "") or "").strip()
    if pii:
        return f"pii:{pii}"
    title = (getattr(paper, "title", "") or "").strip().lower()
    return f"title:{title[:80]}" if title else ""


def papers_to_provider_result(
    papers: List[Any],
    limit: int = 0,
    page: int = 1,
    provenance_by_uid: Optional[Mapping[str, List[str]]] = None,
    per_source_stats: Optional[Dict[str, int]] = None,
    errors: Optional[List[str]] = None,
) -> ProviderSearchResult:
    """Convert an ordered merged MOSAIC pool into one paged provider result.

    Ordering is intentionally preserved. The federated provider constructs a
    deterministic cross-source sequence before this adapter is called; the
    adapter must not add citation- or recency-based pre-ranking ahead of
    KnowledgeSeek's RRF/reranker pipeline.
    """
    provenance_by_uid = provenance_by_uid or {}
    records = [
        paper_to_record(
            paper,
            provenance_sources=provenance_by_uid.get(_paper_uid(paper), []),
        )
        for paper in papers or []
    ]

    total = len(records)
    page = max(1, int(page or 1))
    requested_limit = max(0, int(limit or 0))
    if requested_limit > 0:
        start = (page - 1) * requested_limit
        records = records[start:start + requested_limit]
        metadata_limit = requested_limit
    else:
        metadata_limit = total

    return ProviderSearchResult(
        metadata=SearchMetadata(total=total, page=page, limit=metadata_limit),
        hits=records,
    )
