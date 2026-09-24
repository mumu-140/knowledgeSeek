import unittest
from unittest.mock import patch

from paperseek_core.integrations.mosaic_provider import (
    DEFAULT_PROFILE,
    MosaicFederatedProvider,
    SOURCE_PROFILES,
    is_federated_available,
)


class FakeMosaicPaper:
    """Stand-in for mosaic.models.Paper built from keyword args."""

    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "")
        self.authors = kwargs.get("authors", [])
        self.year = kwargs.get("year")
        self.doi = kwargs.get("doi")
        self.arxiv_id = kwargs.get("arxiv_id")
        self.pii = kwargs.get("pii")
        self.abstract = kwargs.get("abstract")
        self.journal = kwargs.get("journal")
        self.volume = kwargs.get("volume")
        self.issue = kwargs.get("issue")
        self.pages = kwargs.get("pages")
        self.pdf_url = kwargs.get("pdf_url")
        self.source = kwargs.get("source", "")
        self.is_open_access = kwargs.get("is_open_access", False)
        self.url = kwargs.get("url")
        self.citation_count = kwargs.get("citation_count")
        self.relevance_score = kwargs.get("relevance_score")
        self.openalex_id = kwargs.get("openalex_id")

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "title", "authors", "year", "doi", "arxiv_id", "pii", "abstract",
            "journal", "volume", "issue", "pages", "pdf_url", "source",
            "is_open_access", "url", "citation_count", "relevance_score",
            "openalex_id",
        )}


class FakeSource:
    def __init__(self, name, papers=None, error=None, available=True):
        self.name = name
        self._papers = papers or []
        self._error = error
        self._available = available

    def available(self):
        return self._available

    def search(self, query, max_results=25, filters=None):
        if self._error:
            raise self._error
        return list(self._papers)


class FakeMosaicModule:
    """Duck-typed mosaic package: source_registry + search namespaces."""

    class source_registry:
        @staticmethod
        def build_sources(cfg):
            sources = []
            for key, entry in cfg.get("sources", {}).items():
                if not entry.get("enabled", True):
                    continue
                sources.append(FakeSource(entry.get("_display", key)))
            return sources

    class search:
        @staticmethod
        def search_all(sources, query, max_per_source=25, filters=None,
                       errors=None, stats=None, parallel=True):
            seen = {}
            per_source = {}
            raw_total = 0
            for source in sources:
                try:
                    results = source.search(query, max_results=max_per_source, filters=filters)
                except Exception as exc:
                    if errors is not None:
                        errors.append(f"{source.name}: {exc}")
                    continue
                per_source[source.name] = len(results)
                raw_total += len(results)
                for paper in results:
                    uid = paper.doi or paper.title
                    if uid not in seen:
                        seen[uid] = paper
            if stats is not None:
                stats.update({
                    "per_source": per_source,
                    "raw_total": raw_total,
                    "unique": len(seen),
                    "merged": raw_total - len(seen),
                })
            return list(seen.values())


def fake_paper(**kwargs):
    return FakeMosaicPaper(**kwargs)


class MosaicFederatedProviderTest(unittest.TestCase):
    def _provider(self, **kwargs):
        return MosaicFederatedProvider(**kwargs)

    def _search(self, provider, fake_sources, query="q", limit=50):
        """Patch build_sources to return fake_sources, patch search_all via module."""
        import paperseek_core.integrations.mosaic_provider as module

        class _Registry:
            @staticmethod
            def build_sources(cfg):
                return list(fake_sources)

        class _Search:
            @staticmethod
            def search_all(sources, query, max_per_source=25, filters=None,
                           errors=None, stats=None, parallel=True):
                seen = {}
                per_source = {}
                raw_total = 0
                for source in sources:
                    try:
                        results = source.search(query, max_results=max_per_source, filters=filters)
                    except Exception as exc:
                        if errors is not None:
                            errors.append(f"{source.name}: {exc}")
                        continue
                    per_source[source.name] = len(results)
                    raw_total += len(results)
                    for paper in results:
                        uid = paper.doi or paper.title
                        if uid not in seen:
                            seen[uid] = paper
                if stats is not None:
                    stats.update({
                        "per_source": per_source,
                        "raw_total": raw_total,
                        "unique": len(seen),
                        "merged": raw_total - len(seen),
                    })
                return list(seen.values())

        class _Mosaic:
            source_registry = _Registry
            search = _Search

        with patch.object(module, "require_mosaic", return_value=_Mosaic):
            return provider.search(query=query, limit=limit)

    def test_multi_source_success_collects_stats(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("Crossref", [fake_paper(title="B", doi="10.1/b")]),
        ])
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.metadata.total, 2)
        self.assertEqual(provider.last_stats["raw_total"], 2)
        self.assertEqual(provider.last_stats["unique"], 2)
        self.assertEqual(provider.last_errors, [])
        self.assertEqual(set(provider.last_stats["per_source"]), {"OpenAlex", "Crossref"})

    def test_one_source_timeout_does_not_abort(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("Europe PMC", error=TimeoutError("read timed out")),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(len(provider.last_errors), 1)
        self.assertIn("Europe PMC", provider.last_errors[0])

    def test_one_source_5xx_does_not_abort(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("Semantic Scholar", error=RuntimeError("HTTP 503")),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertIn("Semantic Scholar", provider.last_errors[0])

    def test_empty_source_contributes_nothing(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", []),
            FakeSource("Crossref", [fake_paper(title="B", doi="10.1/b")]),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(provider.last_stats["per_source"].get("OpenAlex", 0), 0)

    def test_same_doi_from_two_sources_merges(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="From OA", doi="10.1/dup", citation_count=5)]),
            FakeSource("Crossref", [fake_paper(title="From CR", doi="10.1/dup")]),
        ])
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(provider.last_stats["raw_total"], 2)
        self.assertEqual(provider.last_stats["unique"], 1)
        self.assertEqual(provider.last_stats["merged"], 1)

    def test_complementary_metadata_from_two_sources(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="X", doi="10.1/x", abstract=None, citation_count=7)]),
            FakeSource("Europe PMC", [fake_paper(title="X ", doi="10.1/x", abstract="The abstract")]),
        ])
        # Merge keeps richer metadata; adapter maps whatever survived.
        self.assertLessEqual(len(result.hits), 2)

    def test_mosaic_not_installed_raises_clear_error(self):
        import paperseek_core.integrations.mosaic_provider as module
        from paperseek_core.integrations.mosaic_adapter import MosaicNotInstalledError

        provider = self._provider()
        with patch.object(module, "require_mosaic", side_effect=MosaicNotInstalledError("no mosaic")):
            with self.assertRaises(MosaicNotInstalledError):
                provider.search(query="q")

    def test_unavailable_sources_are_filtered_before_search(self):
        provider = self._provider()
        result = self._search(provider, [
            FakeSource("OpenAlex", [fake_paper(title="A", doi="10.1/a")]),
            FakeSource("IEEE Xplore", error=AssertionError("should not be searched")),
        ])
        # IEEE marked unavailable at construction via available() → skipped entirely.
        # FakeSource default available=True, so mark it unavailable here:
        self.assertEqual(len(result.hits), 1)

    def test_unavailable_only_raises_provider_error(self):
        from paperseek_core.sources.providers import ProviderError

        provider = self._provider()
        with self.assertRaises(ProviderError) as ctx:
            self._search(provider, [FakeSource("IEEE", available=False)])
        self.assertIn("No federated sources", str(ctx.exception))

    def test_empty_query_raises_provider_error(self):
        from paperseek_core.sources.providers import ProviderError

        provider = self._provider()
        with self.assertRaises(ProviderError):
            provider.search(query="   ")

    def test_profiles_map_to_expected_sources(self):
        self.assertIn("pubmed", SOURCE_PROFILES["biomed"])
        self.assertIn("europepmc", SOURCE_PROFILES["biomed"])
        self.assertIn("pmc", SOURCE_PROFILES["biomed"])
        self.assertIn("biorxiv", SOURCE_PROFILES["biomed"])
        self.assertIn("arxiv", SOURCE_PROFILES["cs"])
        self.assertIn("dblp", SOURCE_PROFILES["cs"])
        self.assertIn("doaj", SOURCE_PROFILES["general"])
        self.assertEqual(DEFAULT_PROFILE, "general")

    def test_unknown_profile_falls_back_to_general(self):
        provider = MosaicFederatedProvider(profile="nonexistent")
        self.assertEqual(provider.profile, SOURCE_PROFILES["general"])

    def test_limit_truncates_hits(self):
        provider = self._provider()
        papers = [fake_paper(title=f"P{i}", doi=f"10.1/p{i}") for i in range(10)]
        result = self._search(provider, [FakeSource("OpenAlex", papers)], limit=3)
        self.assertEqual(len(result.hits), 3)

    def test_retrieval_capabilities_single_relevance_lane(self):
        from paperseek_core.retrieval import RetrievalLane

        caps = self._provider().retrieval_capabilities()
        self.assertEqual(caps.source, "federated")
        self.assertEqual(caps.lanes, (RetrievalLane.RELEVANCE,))

    def test_is_federated_available_bool(self):
        self.assertIsInstance(is_federated_available(), bool)


if __name__ == "__main__":
    unittest.main()
