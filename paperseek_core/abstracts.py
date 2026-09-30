from typing import Optional, Dict
import requests


class AbstractFetcher:
    """Fetch abstracts by DOI from Crossref, with in-memory caching."""

    def __init__(self, egress_router=None):
        self.egress_router = egress_router
        self._cache: Dict[str, Optional[str]] = {}

    def fetch(self, doi: str) -> Optional[str]:
        if not doi:
            return None
        if doi in self._cache:
            return self._cache[doi]

        abstract = self._try_crossref(doi)
        self._cache[doi] = abstract
        return abstract

    def _try_crossref(self, doi: str) -> Optional[str]:
        try:
            url = f"https://api.crossref.org/works/{doi}"
            resp = (
                self.egress_router.request("GET", url, timeout=10)
                if self.egress_router is not None
                else requests.get(url, timeout=10)
            )
            resp.raise_for_status()
            msg = resp.json().get("message", {})
            abstract = msg.get("abstract", "")
            if abstract and abstract.strip() and len(abstract.strip()) > 20:
                return self._clean_xml(abstract.strip())
        except Exception:
            pass
        return None

    @staticmethod
    def _clean_xml(text: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = re.sub(r"\s+", " ", text).strip()
        return text
