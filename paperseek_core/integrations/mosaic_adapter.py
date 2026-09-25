"""Adapter between MOSAIC ``Paper`` objects and KnowledgeSeek ``PaperRecord``.

MOSAIC stays an optional dependency: importing this module never imports
``mosaic``.  Callers first check :func:`mosaic_available`, then pass already
constructed ``mosaic.models.Paper`` instances (or plain dicts via
``Paper.from_dict``) to the conversion helpers.

Only data mapping lives here: no ranking, no RRF, no embedding, no LLM,
no citation expansion, no query generation, no network access.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    """Raised when the optional ``mosaic`` package is required but missing."""


def mosaic_available() -> bool:
    """Return True when the optional ``mosaic`` package can be imported."""
    try:
        import mosaic.models  # noqa: F401
        import mosaic.search  # noqa: F401
        import mosaic.source_registry  # noqa: F401
    except Exception:
        return False
    return True


def require_mosaic():
    """Import the ``mosaic`` package (with federated submodules) or raise."""
    try:
        import mosaic
        import mosaic.models  # noqa: F401
        import mosaic.search  # noqa: F401
        import mosaic.source_registry  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on environment
        raise MosaicNotInstalledError(
            "The 'mosaic' package is required for federated retrieval. "
            "Install it with: pip install 'paperseek[federated]'"
        ) from exc
    return mosaic


def mosaic_paper_from_dict(payload: Dict[str, Any]):
    """Rebuild a ``mosaic.models.Paper`` from its ``to_dict()`` form.

    Keeps tests and provider code free of a hard ``mosaic`` import while the
    package itself is absent; raises :class:`MosaicNotInstalledError` when it
    really is missing.
    """
    mosaic = require_mosaic()
    return mosaic.models.Paper.from_dict(payload)


def paper_to_record(paper: Any, limit_hint: int = 0) -> PaperRecord:
    """Convert a ``mosaic.models.Paper`` into a KnowledgeSeek ``PaperRecord``.

    Field mapping follows the Phase 1 audit table: identifiers drive dedup,
    the originating MOSAIC source name is preserved in ``raw["mosaic_source"]``
    (and as citation db label), and the original payload stays in ``raw``.
    """
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


def papers_to_provider_result(
    papers: List[Any],
    limit: int = 0,
    per_source_stats: Optional[Dict[str, int]] = None,
    errors: Optional[List[str]] = None,
) -> ProviderSearchResult:
    """Convert merged MOSAIC ``Paper`` objects into a ``ProviderSearchResult``.

    ``search_all`` returns papers in merge/insertion order, which under
    parallel fan-out is thread-completion order — nondeterministic run to
    run.  Before the ``limit`` truncation the hits are put in a deterministic
    metadata order (cross-source merge wins, then citations, then recency,
    then uid) so the same query yields the same first ``limit`` hits.  This
    is a stable data-layer ordering, not relevance ranking: text relevance
    stays with KnowledgeSeek's RRF/reranker downstream.
    """
    records = [paper_to_record(paper) for paper in papers or []]
    records.sort(key=lambda record: _deterministic_sort_key(record))
    if limit and limit > 0:
        records = records[:limit]
    return ProviderSearchResult(
        metadata=SearchMetadata(total=len(records), page=1, limit=limit or len(records)),
        hits=records,
    )


def _deterministic_sort_key(record: PaperRecord) -> tuple:
    """Stable, relevance-free ordering key for merged federated hits."""
    try:
        citations = int(record.citations[0].count) if record.citations else 0
    except (TypeError, ValueError, IndexError):
        citations = 0
    year = getattr(record.source, "publish_year", None)
    year = int(year) if year else 0
    return (-citations, -year, record.uid or record.title or "")
