"""MosaicFederatedProvider: KnowledgeSeek provider backed by MOSAIC fan-out.

Wraps ``mosaic.source_registry.build_sources`` + ``mosaic.search.search_all``
behind the same ``search(query, limit, page, lane)`` interface the other
KnowledgeSeek providers implement.  MOSAIC does retrieve → merge only; the
resulting ``PaperRecord`` pool feeds the existing KnowledgeSeek retrieval
fusion (RRF / embeddings / reranker / LLM ranking) unchanged.
"""

from __future__ import annotations

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

# MOSAIC source-registry keys (source_registry._SOURCE_REGISTRY), not display names.
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

# Every registry key that build_sources knows; used to explicitly disable
# sources outside the active profile (unlisted keys default to enabled).
_ALL_REGISTRY_KEYS: Tuple[str, ...] = (
    "arxiv", "semantic_scholar", "sciencedirect", "doaj", "europepmc", "openalex",
    "base", "core", "nasa_ads", "ieee", "zenodo", "springer_api", "crossref",
    "dblp", "hal", "pubmed", "pmc", "biorxiv", "pedro", "scopus",
)


class MosaicFederatedProvider:
    """Multi-source retrieval provider delegating fan-out/merge to MOSAIC."""

    def __init__(
        self,
        profile: str = DEFAULT_PROFILE,
        max_per_source: int = 25,
        parallel: bool = True,
        email: str = "",
    ):
        self.profile = SOURCE_PROFILES.get((profile or "").strip().lower(), SOURCE_PROFILES[DEFAULT_PROFILE])
        self.max_per_source = max(1, int(max_per_source or 25))
        self.parallel = bool(parallel)
        self.email = email or ""
        self.last_stats: Dict[str, Any] = {}
        self.last_errors: List[str] = []
        self.last_latency: float = 0.0

    # -- KnowledgeSeek provider interface ---------------------------------

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

        mosaic = require_mosaic()
        sources = self._build_sources(mosaic)
        active = [s for s in sources if s.available()]
        if not active:
            from paperseek_core.sources.providers import ProviderError

            raise ProviderError(
                PROVIDER_ID,
                "No federated sources are available; check the profile and API keys.",
            )

        errors: List[str] = []
        stats: Dict[str, Any] = {}
        started = time.time()
        papers = mosaic.search.search_all(
            sources=active,
            query=query,
            max_per_source=max(self.max_per_source, int(limit or 0)),
            filters=None,
            errors=errors,
            stats=stats,
            parallel=self.parallel,
        )
        self.last_latency = time.time() - started
        self.last_errors = list(errors)
        self.last_stats = dict(stats)

        return papers_to_provider_result(papers, limit=max(int(limit or 0), 0))

    # -- internals --------------------------------------------------------

    def _build_sources(self, mosaic: Any) -> List[Any]:
        """Build MOSAIC sources restricted to the active profile.

        Registry keys not in the profile are explicitly disabled because
        ``build_sources`` defaults unlisted keys to enabled.
        """
        cfg: Dict[str, Any] = {"sources": {}}
        for key in _ALL_REGISTRY_KEYS:
            enabled = key in self.profile
            entry: Dict[str, Any] = {"enabled": enabled}
            if enabled:
                entry["api_key"] = ""
            cfg["sources"][key] = entry
        if self.email:
            cfg["unpaywall"] = {"email": self.email}
        return mosaic.source_registry.build_sources(cfg)


def is_federated_available() -> bool:
    """True when MOSAIC is importable, i.e. federated mode can run here."""
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
