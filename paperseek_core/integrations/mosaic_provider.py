"""MosaicFederatedProvider: KnowledgeSeek provider backed by MOSAIC fan-out.

The provider keeps MOSAIC focused on source adapters and metadata merging while
KnowledgeSeek remains responsible for retrieval fusion, reranking, and LLM
ranking. Federated snapshots are cached per query so KnowledgeSeek can page
through the full merged candidate pool instead of truncating it before RRF.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
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
        self.profile = SOURCE_PROFILES.get(
            (profile or "").strip().lower(),
            SOURCE_PROFILES[DEFAULT_PROFILE],
        )
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
            self._refresh_snapshot(query, fetch_per_source)

        self.last_stats = dict(self._snapshot_stats)
        self.last_errors = list(self._snapshot_errors)
        self.last_latency = self._snapshot_latency

        return papers_to_provider_result(
            self._snapshot_papers,
            limit=limit,
            page=page,
            provenance_by_uid=self._snapshot_provenance,
        )

    def _refresh_snapshot(self, query: str, fetch_per_source: int) -> None:
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
    ) -> Tuple[List[Any], Dict[str, List[str]], List[str], Dict[str, Any]]:
        """Query sources concurrently, then merge in deterministic rank order.

        Retrieval remains parallel. Only the merge step is ordered: rank 1 from
        each source in profile order, then rank 2, and so on. This removes
        thread-completion-order drift while preserving each source's own result
        ranking and avoiding a second relevance model before KnowledgeSeek RRF.
        """
        results_by_source: Dict[str, List[Any]] = {}
        errors: List[str] = []

        def run(source: Any) -> List[Any]:
            return list(source.search(query, max_results=max_per_source, filters=None) or [])

        if self.parallel and len(active) > 1:
            workers = min(len(active), 8)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run, source): source for source in active}
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        results_by_source[source.name] = future.result()
                    except Exception as exc:
                        errors.append(f"{source.name}: {exc}")
        else:
            for source in active:
                try:
                    results_by_source[source.name] = run(source)
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")

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
            "per_source": per_source,
            "raw_total": raw_total,
            "unique": len(papers),
            "merged": raw_total - len(papers),
            "after_filters": len(papers),
        }
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
