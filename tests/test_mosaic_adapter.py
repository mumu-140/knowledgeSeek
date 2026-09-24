import unittest

from paperseek_core.integrations.mosaic_adapter import (
    MosaicNotInstalledError,
    PROVIDER_ID,
    mosaic_available,
    mosaic_paper_from_dict,
    paper_to_record,
    papers_to_provider_result,
)


def make_paper(**kwargs):
    """Build a mosaic Paper via from_dict when mosaic is importable.

    Falls back to a duck-typed stand-in when mosaic is absent so mapping tests
    still run on minimal installs; the from_dict path is covered separately.
    """
    payload = {
        "title": "Default Title",
        "authors": [],
        "year": None,
        "doi": None,
        "arxiv_id": None,
        "pii": None,
        "abstract": None,
        "journal": None,
        "volume": None,
        "issue": None,
        "pages": None,
        "pdf_url": None,
        "source": "",
        "is_open_access": False,
        "url": None,
        "citation_count": None,
        "relevance_score": None,
        "openalex_id": None,
    }
    payload.update(kwargs)

    class _StandIn:
        def __init__(self, data):
            self.__dict__.update(data)
            self._data = data

        def to_dict(self):
            return dict(self._data)

    try:
        return mosaic_paper_from_dict(payload)
    except MosaicNotInstalledError:
        return _StandIn(payload)


class MosaicAdapterTest(unittest.TestCase):
    def test_doi_paper_maps_identifiers_and_citations(self):
        record = paper_to_record(make_paper(
            title="Federated Retrieval",
            authors=["Ada Lovelace", "Alan Turing"],
            year=2023,
            doi="10.1234/abcd",
            journal="Nature Methods",
            citation_count=42,
            source="OpenAlex",
        ))
        self.assertEqual(record.title, "Federated Retrieval")
        self.assertEqual(record.provider, PROVIDER_ID)
        self.assertEqual(record.identifiers.doi, "10.1234/abcd")
        self.assertEqual(record.source.publish_year, 2023)
        self.assertEqual(record.source.source_title, "Nature Methods")
        self.assertEqual(
            [a.display_name for a in record.names.authors],
            ["Ada Lovelace", "Alan Turing"],
        )
        self.assertEqual(len(record.citations), 1)
        self.assertEqual(record.citations[0].db, "OpenAlex")
        self.assertEqual(record.citations[0].count, 42)
        self.assertEqual(record.raw.get("mosaic_source"), "OpenAlex")

    def test_arxiv_paper_maps_arxiv_identifier(self):
        record = paper_to_record(make_paper(
            title="Attention Is Off By One",
            arxiv_id="2401.00001",
            abstract="Abstract text.",
        ))
        self.assertEqual(record.identifiers.arxiv, "2401.00001")
        self.assertEqual(record.abstract, "Abstract text.")
        self.assertEqual(record.identifiers.doi, "")
        self.assertEqual(record.uid, "2401.00001")

    def test_openalex_id_paper(self):
        record = paper_to_record(make_paper(
            title="Indexed Work",
            openalex_id="W123",
            url="https://openalex.org/W123",
        ))
        self.assertEqual(record.identifiers.openalex, "W123")
        self.assertEqual(record.links.landing_page, "https://openalex.org/W123")
        self.assertEqual(record.links.record, "https://openalex.org/W123")

    def test_doi_url_is_normalized(self):
        record = paper_to_record(make_paper(
            title="Normalization",
            doi="https://doi.org/10.1/x",
        ))
        self.assertEqual(record.identifiers.doi, "10.1/x")
        self.assertEqual(record.uid, "10.1/x")

    def test_paper_without_doi_falls_back_to_title_uid(self):
        record = paper_to_record(make_paper(title="Only Title"))
        self.assertEqual(record.uid, "Only Title")
        self.assertEqual(record.identifiers.doi, "")

    def test_missing_abstract_and_authors_stay_empty(self):
        record = paper_to_record(make_paper(title="Sparse"))
        self.assertEqual(record.abstract, "")
        self.assertEqual(record.names.authors, [])
        self.assertEqual(record.citations, [])

    def test_zero_citation_count_omits_citation_entry(self):
        record = paper_to_record(make_paper(title="Uncited", citation_count=0))
        self.assertEqual(record.citations, [])

    def test_oa_pdf_link_maps_to_pdf(self):
        record = paper_to_record(make_paper(
            title="Open Access",
            pdf_url="https://example.org/paper.pdf",
        ))
        self.assertEqual(record.links.pdf, "https://example.org/paper.pdf")

    def test_unicode_title_survives(self):
        title = "Étude sur la 检索 — naïve approach"
        record = paper_to_record(make_paper(title=title))
        self.assertEqual(record.title, title)

    def test_duplicate_identifier_papers_dedup_downstream_ready(self):
        # Two papers sharing a DOI produce records whose KnowledgeSeek
        # document_key() collides — dedup happens in retrieval, not adapter.
        a = paper_to_record(make_paper(title="Version A", doi="10.1/dup"))
        b = paper_to_record(make_paper(title="Version B", doi="10.1/dup"))
        self.assertEqual(a.identifiers.doi, b.identifiers.doi)

    def test_papers_to_provider_result_shapes_metadata(self):
        papers = [
            make_paper(title="P1", doi="10.1/p1"),
            make_paper(title="P2", arxiv_id="2401.2"),
            make_paper(title="P3"),
        ]
        result = papers_to_provider_result(papers, limit=2)
        self.assertEqual(result.metadata.total, 2)
        self.assertEqual(result.metadata.page, 1)
        self.assertEqual(result.metadata.limit, 2)
        self.assertEqual([h.title for h in result.hits], ["P1", "P2"])

    def test_papers_to_provider_result_empty(self):
        result = papers_to_provider_result([])
        self.assertEqual(result.hits, [])
        self.assertEqual(result.metadata.total, 0)

    def test_raw_preserves_original_payload(self):
        record = paper_to_record(make_paper(title="Raw Keeper", pii="S0001"))
        self.assertEqual(record.raw.get("pii"), "S0001")
        self.assertEqual(record.raw.get("title"), "Raw Keeper")

    def test_mosaic_available_returns_bool(self):
        self.assertIsInstance(mosaic_available(), bool)

    def test_require_mosaic_error_message_when_missing(self):
        # Only meaningful on installs without mosaic; skip when importable.
        if mosaic_available():
            self.skipTest("mosaic is installed in this environment")
        with self.assertRaises(MosaicNotInstalledError) as ctx:
            mosaic_paper_from_dict({"title": "x"})
        self.assertIn("paperseek[federated]", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
