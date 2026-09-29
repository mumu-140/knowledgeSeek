"""MosaicFederatedProvider: KnowledgeSeek provider backed by MOSAIC fan-out.

The provider keeps MOSAIC focused on source adapters and metadata merging while
KnowledgeSeek remains responsible for retrieval fusion, reranking, and LLM
ranking. Federated snapshots are cached per query so KnowledgeSeek can page
through the full merged candidate pool instead of truncating it before RRF.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from paperseek_core.integrations.mosaic_adapter import (
    PROVIDER_ID,
    MosaicNotInstalledError,
    mosaic_available,
    papers_to_provider_result,
    require_mosaic,
)
from paperseek_core.retrieval import RetrievalLane
from paperseek_core.sources.providers import ProviderRetrievalCapabilities


PROFILE_BIOMED: Tuple[str, ...] = (
    "pubmed", "europepmc", "pmc", "openalex", "semantic_scholar", "biorxiv", "crossref",
)
PROFILE_CS: Tuple[str, ...] = (
    "openalex", "semantic_scholar", "arxiv", "dblp", "crossref",
)
PROFILE_GENERAL: Tuple[str, ...] = (
    "openalex", "semantic_scholar", "crossref", "doaj",
)

SOURCE_PROFILES: Dict[str, Tuple[str, ...]] = {
    "biomed": PROFILE_BIOMED,
    "cs": PROFILE_CS,
    "general": PROFILE_GENERAL,
}
DEFAULT_PROFILE = "general"

# Upstream adapters report failures as free-form text ("HTTP 503: ...",
# "(429) Too Many Requests"). Match whole 3-digit codes only, so counts such as
# "5000 results" or "50 papers" are never read as a 5xx status.
_STATUS_CODE_RE = re.compile(r"(?<!\d)([1-5]\d{2})(?!\d)")
_UPSTREAM_SERVER_ERROR_CODES: Tuple[int, ...] = (500, 501, 502, 503, 504)


def _error_status_codes(text: str) -> frozenset:
    """Return the whole HTTP status codes mentioned in an error message."""
    return frozenset(int(code) for code in _STATUS_CODE_RE.findall(text))


_ALL_REGISTRY_KEYS: Tuple[str, ...] = (
    "arxiv", "semantic_scholar", "sciencedirect", "doaj", "europepmc", "openalex",
    "base", "core", "nasa_ads", "ieee", "zenodo", "springer_api", "crossref",
    "dblp", "hal", "pubmed", "pmc", "biorxiv", "pedro", "scopus",
)


class MosaicFederatedProvider:
    """Multi-source retrieval provider delegating source access to MOSAIC."""

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE,
        max_per_source: int = 25,
        parallel: bool = True,
        email: str = "",
        openalex_email: str = "",
        crossref_email: str = "",
        semantic_scholar_api_key: str = "",
        pubmed_api_key: str = "",
    ):
        profile_key = (profile or "").strip().lower()
        self.profile_name = profile_key if profile_key in SOURCE_PROFILES else DEFAULT_PROFILE
        self.profile = SOURCE_PROFILES[self.profile_name]
        self.max_per_source = max(1, int(max_per_source or 25))
        self.parallel = bool(parallel)

        common_email = email or ""
        self.openalex_email = openalex_email or common_email
        self.crossref_email = crossref_email or common_email
        self.source_api_keys: Dict[str, str] = {
            "semantic_scholar": semantic_scholar_api_key or "",
            "pubmed": pubmed_api_key or "",
            "pmc": pubmed_api_key or "",
        }

        self.last_stats: Dict[str, Any] = {}
        self.last_errors: List[str] = []
        self.last_latency: float = 0.0

        self._snapshot_query: str = ""
        self._snapshot_fetch_per_source: int = 0
        self._snapshot_papers: List[Any] = []
        self._snapshot_provenance: Dict[str, List[str]] = {}
        self._snapshot_stats: Dict[str, Any] = {}
        self._snapshot_errors: List[str] = []
        self._snapshot_latency: float = 0.0

    def retrieval_capabilities(self) -> ProviderRetrievalCapabilities:
        return ProviderRetrievalCapabilities(
            source=PROVIDER_ID,
            lanes=(RetrievalLane.RELEVANCE,),
        )

    def search(
        self,
        query: str,
        limit: int = 50,
        page: int = 1,
        lane: str = RetrievalLane.RELEVANCE,
        event_handler: Optional[Any] = None,
        **kwargs: Any,
    ):
        query = (query or "").strip()
        if not query:
            from paperseek_core.sources.providers import ProviderError

            raise ProviderError(PROVIDER_ID, "Federated search query is empty.")

        page = max(1, int(page or 1))
        limit = max(1, int(limit or 50))
        fetch_per_source = max(self.max_per_source, limit)

        if (
            query != self._snapshot_query
            or fetch_per_source > self._snapshot_fetch_per_source
        ):
            self._refresh_snapshot(query, fetch_per_source, event_handler=event_handler)

        self.last_stats = dict(self._snapshot_stats)
        self.last_errors = list(self._snapshot_errors)
        self.last_latency = self._snapshot_latency

        return papers_to_provider_result(
            self._snapshot_papers,
            limit=limit,
            page=page,
            provenance_by_uid=self._snapshot_provenance,
        )

    def _refresh_snapshot(self, query: str, fetch_per_source: int, event_handler: Optional[Any] = None) -> None:
        mosaic = require_mosaic()
        sources = self._build_sources(mosaic)
        active = [source for source in sources if source.available()]
        if not active:
            from paperseek_core.sources.providers import ProviderError

            raise ProviderError(
                PROVIDER_ID,
                "No federated sources are available; check the profile and API keys.",
            )

        started = time.time()
        papers, provenance, errors, stats = self._search_sources(
            mosaic,
            active,
            query=query,
            max_per_source=fetch_per_source,
            event_handler=event_handler,
        )
        latency = time.time() - started

        self._snapshot_query = query
        self._snapshot_fetch_per_source = fetch_per_source
        self._snapshot_papers = papers
        self._snapshot_provenance = provenance
        self._snapshot_stats = stats
        self._snapshot_errors = errors
        self._snapshot_latency = latency

    def _search_sources(
        self,
        mosaic: Any,
        active: List[Any],
        *,
        query: str,
        max_per_source: int,
        event_handler: Optional[Any] = None,
    ) -> Tuple[List[Any], Dict[str, List[str]], List[str], Dict[str, Any]]:
        """Query sources concurrently, then merge in deterministic rank order.

        Retrieval remains parallel. Only the merge step is ordered: rank 1 from
        each source in profile order, then rank 2, and so on. This removes
        thread-completion-order drift while preserving each source's own result
        ranking and avoiding a second relevance model before KnowledgeSeek RRF.
        """
        results_by_source: Dict[str, List[Any]] = {}
        errors: List[str] = []

        def _friendly_status(count: int, exc: Optional[Exception] = None) -> Tuple[str, str]:
            if exc is not None:
                err_str = str(exc).strip()
                err_lower = err_str.lower()
                codes = _error_status_codes(err_str)
                exc_name = type(exc).__name__.lower()
                if 429 in codes or "rate limit" in err_lower or "too many requests" in err_lower:
                    return "rate-limited", "Rate limited (429)"
                if "timeout" in exc_name or "timeout" in err_lower or "timed out" in err_lower:
                    return "error", "Request timed out"
                if 404 in codes:
                    return "empty", "0 papers"
                server_codes = sorted(codes.intersection(_UPSTREAM_SERVER_ERROR_CODES))
                if server_codes:
                    return "error", f"Upstream server error ({server_codes[0]})"
                if "server error" in err_lower:
                    return "error", "Upstream server error"
                return "error", f"Error: {err_str[:40]}"
            if count == 0:
                return "empty", "0 papers"
            return "success", f"{count} papers"

        source_details: Dict[str, Dict[str, Any]] = {
            source.name: {"status": "searching", "count": 0, "detail": "Querying..."}
            for source in active
        }

        if event_handler:
            event_handler({
                "type": "stage",
                "stage": "federated_retrieval",
                "status": "processing",
                "data": {
                    "profile": getattr(self, "profile_name", "general"),
                    "sources": dict(source_details),
                    "raw_total": 0,
                    "unique": 0,
                    "merged": 0,
                },
            })

        def run(source: Any) -> List[Any]:
            return list(source.search(query, max_results=max_per_source, filters=None) or [])

        if self.parallel and len(active) > 1:
            workers = min(len(active), 8)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run, source): source for source in active}
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        res = future.result()
                        results_by_source[source.name] = res
                        st, dt = _friendly_status(len(res))
                        source_details[source.name] = {"status": st, "count": len(res), "detail": dt}
                    except Exception as exc:
                        errors.append(f"{source.name}: {exc}")
                        st, dt = _friendly_status(0, exc)
                        source_details[source.name] = {"status": st, "count": 0, "detail": dt}
                    if event_handler:
                        event_handler({
                            "type": "stage",
                            "stage": "federated_retrieval",
                            "status": "processing",
                            "data": {
                                "profile": getattr(self, "profile_name", "general"),
                                "sources": dict(source_details),
                                "raw_total": sum(s["count"] for s in source_details.values()),
                                "unique": 0,
                                "merged": 0,
                            },
                        })
        else:
            for source in active:
                try:
                    res = run(source)
                    results_by_source[source.name] = res
                    st, dt = _friendly_status(len(res))
                    source_details[source.name] = {"status": st, "count": len(res), "detail": dt}
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")
                    st, dt = _friendly_status(0, exc)
                    source_details[source.name] = {"status": st, "count": 0, "detail": dt}
                if event_handler:
                    event_handler({
                        "type": "stage",
                        "stage": "federated_retrieval",
                        "status": "processing",
                        "data": {
                            "profile": getattr(self, "profile_name", "general"),
                            "sources": dict(source_details),
                            "raw_total": sum(s["count"] for s in source_details.values()),
                            "unique": 0,
                            "merged": 0,
                        },
                    })

        per_source = {
            source.name: len(results_by_source[source.name])
            for source in active
            if source.name in results_by_source
        }
        raw_total = sum(per_source.values())

        seen: Dict[str, Any] = {}
        provenance: Dict[str, List[str]] = {}
        max_rank = max((len(values) for values in results_by_source.values()), default=0)

        for rank in range(max_rank):
            for source in active:
                results = results_by_source.get(source.name) or []
                if rank >= len(results):
                    continue
                paper = results[rank]
                uid = paper.uid
                source_list = provenance.setdefault(uid, [])
                if source.name not in source_list:
                    source_list.append(source.name)
                mosaic.services.merge_papers(seen, paper)

        papers = list(seen.values())
        stats = {
            "profile": getattr(self, "profile_name", "general"),
            "per_source": per_source,
            "source_status": {k: v["status"] for k, v in source_details.items()},
            "source_details": source_details,
            "raw_total": raw_total,
            "unique": len(papers),
            "merged": raw_total - len(papers),
            "after_filters": len(papers),
            "errors": errors,
        }
        if event_handler:
            event_handler({
                "type": "stage",
                "stage": "federated_retrieval",
                "status": "complete",
                "data": {
                    "profile": getattr(self, "profile_name", "general"),
                    "sources": dict(source_details),
                    "raw_total": raw_total,
                    "unique": len(papers),
                    "merged": raw_total - len(papers),
                },
            })
        return papers, provenance, errors, stats

    def _build_sources(self, mosaic: Any) -> List[Any]:
        """Build MOSAIC sources restricted to the active profile."""
        cfg: Dict[str, Any] = {"sources": {}}
        for key in _ALL_REGISTRY_KEYS:
            enabled = key in self.profile
            entry: Dict[str, Any] = {"enabled": enabled}
            if enabled:
                entry["api_key"] = self.source_api_keys.get(key, "")
            cfg["sources"][key] = entry

        common_email = self.openalex_email or self.crossref_email
        if common_email:
            cfg["unpaywall"] = {"email": common_email}

        sources = mosaic.source_registry.build_sources(cfg)

        # MOSAIC 1.5.x uses one unpaywall email slot for both sources. Preserve
        # KnowledgeSeek's separate polite-pool settings when both are supplied.
        for source in sources:
            if source.name == "OpenAlex" and self.openalex_email:
                setattr(source, "_email", self.openalex_email)
            elif source.name == "Crossref" and self.crossref_email:
                setattr(source, "_email", self.crossref_email)
        return sources


def is_federated_available() -> bool:
    return mosaic_available()


__all__ = [
    "MosaicFederatedProvider",
    "MosaicNotInstalledError",
    "PROFILE_BIOMED",
    "PROFILE_CS",
    "PROFILE_GENERAL",
    "SOURCE_PROFILES",
    "DEFAULT_PROFILE",
    "is_federated_available",
]
