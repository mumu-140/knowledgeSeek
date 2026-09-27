#!/usr/bin/env python
"""Benchmark single-source baseline vs federated MOSAIC retrieval.

The federated side is collected through the same provider pagination contract
used by KnowledgeSeek. Metrics therefore describe candidates that are actually
reachable by the downstream RRF/reranker pipeline, not only MOSAIC's internal
pre-pagination pool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paperseek_core.integrations.mosaic_provider import MosaicFederatedProvider  # noqa: E402
from paperseek_core.sources.providers import OpenAlexProvider  # noqa: E402


def _doi(record) -> str:
    return (getattr(record.identifiers, "doi", "") or "").strip().lower()


def _fingerprint_set(records) -> set:
    keys = set()
    for record in records:
        doi = _doi(record)
        key = f"doi:{doi}" if doi else f"title:{(record.title or '').strip().lower()}"
        if key and key != "title:":
            keys.add(key)
    return keys


def _completeness(records) -> dict:
    n = len(records) or 1
    return {
        "doi_pct": round(100 * sum(1 for r in records if _doi(r)) / n, 1),
        "abstract_pct": round(
            100 * sum(1 for r in records if (r.abstract or "").strip()) / n,
            1,
        ),
        "year_pct": round(
            100 * sum(
                1 for r in records if getattr(r.source, "publish_year", None)
            ) / n,
            1,
        ),
        "authors_pct": round(
            100 * sum(1 for r in records if getattr(r.names, "authors", None)) / n,
            1,
        ),
    }


def _top_keys(records, n=20) -> set:
    return _fingerprint_set(records[:n])


def run_baseline(provider, query: str, limit: int) -> dict:
    started = time.time()
    try:
        result = provider.search(query, limit=limit)
        records = list(result.hits)
        error = None
    except Exception as exc:
        records, error = [], f"{type(exc).__name__}: {exc}"
    latency = round(time.time() - started, 2)
    return {
        "records": records,
        "raw": len(records),
        "unique": len(_fingerprint_set(records)),
        "latency_s": latency,
        "error": error,
    }


def run_federated(
    profile: str,
    query: str,
    limit: int,
    max_per_source: int,
    openalex_email: str,
) -> dict:
    provider = MosaicFederatedProvider(
        profile=profile,
        max_per_source=max_per_source,
        openalex_email=openalex_email,
        crossref_email=os.environ.get("CROSSREF_EMAIL", ""),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", ""),
        pubmed_api_key=os.environ.get("PUBMED_API_KEY", ""),
    )

    started = time.time()
    records = []
    first_page_records = []
    error = None
    total = 0
    page = 1

    try:
        while True:
            result = provider.search(query, limit=limit, page=page)
            hits = list(result.hits)
            if page == 1:
                first_page_records = list(hits)
            records.extend(hits)
            total = int(getattr(result.metadata, "total", 0) or 0)
            if not hits or len(records) >= total:
                break
            page += 1
            if page > 100:
                raise RuntimeError("Federated pagination exceeded 100 pages")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    wall = round(time.time() - started, 2)
    stats = provider.last_stats or {}
    raw_total = int(stats.get("raw_total") or 0)
    source_unique = int(stats.get("unique") or 0)
    per_source = stats.get("per_source") or {}
    accessible_unique = len(_fingerprint_set(records))

    return {
        "records": records,
        "first_page_records": first_page_records,
        "raw": raw_total,
        "unique": accessible_unique,
        "source_unique": source_unique,
        "metadata_total": total,
        "pages": page if records else 0,
        "latency_s": round(provider.last_latency, 2) if provider.last_latency else wall,
        "wall_latency_s": wall,
        "error": error,
        "source_distribution": per_source,
        "source_error_count": len(provider.last_errors or []),
        "errors": [
            error_text.split("\n")[0][:120]
            for error_text in (provider.last_errors or [])
        ],
    }


def compare(query: dict, baseline: dict, federated: dict) -> dict:
    base_keys = _fingerprint_set(baseline["records"])
    fed_keys = _fingerprint_set(federated["records"])
    new_keys = fed_keys - base_keys
    top_overlap = (
        _top_keys(baseline["records"])
        & _top_keys(federated["first_page_records"])
    )
    duplicate_rate = round(
        100 * (federated["raw"] - federated["source_unique"])
        / max(1, federated["raw"]),
        1,
    )
    return {
        "id": query["id"],
        "kind": query.get("kind"),
        "query": query["query"],
        "profile": query.get("profile", "general"),
        "baseline": {
            "raw": baseline["raw"],
            "unique": baseline["unique"],
            "latency_s": baseline["latency_s"],
            "error": baseline["error"],
            "completeness": _completeness(baseline["records"]),
        },
        "federated": {
            "raw": federated["raw"],
            "unique": federated["unique"],
            "source_unique": federated["source_unique"],
            "metadata_total": federated["metadata_total"],
            "pages": federated["pages"],
            "latency_s": federated["latency_s"],
            "wall_latency_s": federated["wall_latency_s"],
            "source_distribution": federated["source_distribution"],
            "source_error_count": federated["source_error_count"],
            "errors": federated["errors"],
            "error": federated["error"],
            "completeness": _completeness(federated["records"]),
        },
        "new_unique_papers": len(new_keys),
        "new_unique_pct": round(100 * len(new_keys) / max(1, len(fed_keys)), 1),
        "top20_overlap_count": len(top_overlap),
        "doi_overlap_count": len(
            base_keys & fed_keys & {key for key in fed_keys if key.startswith("doi:")}
        ),
        "duplicate_rate_pct": duplicate_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-per-source", type=int, default=10)
    parser.add_argument("--baseline", default="openalex", choices=["openalex"])
    parser.add_argument("--out", default="federated_benchmark_results.json")
    parser.add_argument(
        "--queries",
        default=str(ROOT / "tests" / "fixtures" / "federated_queries.json"),
    )
    parser.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()

    with open(args.queries, encoding="utf-8") as handle:
        spec = json.load(handle)
    queries = spec["queries"]

    openalex_email = os.environ.get("OPENALEX_EMAIL", "")
    baseline_provider = OpenAlexProvider(email=openalex_email)

    results = []
    for query in queries:
        print(
            f"--- {query['id']} [{query.get('kind')}] {query['query']}",
            flush=True,
        )
        baseline = run_baseline(baseline_provider, query["query"], args.limit)
        federated = run_federated(
            query.get("profile", "general"),
            query["query"],
            args.limit,
            args.max_per_source,
            openalex_email,
        )
        row = compare(query, baseline, federated)
        if "expected_doi" in query:
            fed_dois = {_doi(record) for record in federated["records"]}
            row["expected_doi_found"] = query["expected_doi"].lower() in fed_dois
            base_dois = {_doi(record) for record in baseline["records"]}
            row["expected_doi_in_baseline"] = (
                query["expected_doi"].lower() in base_dois
            )
        results.append(row)
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "new_unique_papers": row["new_unique_papers"],
                    "new_unique_pct": row["new_unique_pct"],
                    "top20_overlap_count": row["top20_overlap_count"],
                    "federated_unique": row["federated"]["unique"],
                    "pages": row["federated"]["pages"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(args.pause)

    summary = {
        "config": {
            "limit": args.limit,
            "max_per_source": args.max_per_source,
            "baseline": args.baseline,
            "queries_file": args.queries,
        },
        "totals": {
            "queries": len(results),
            "avg_new_unique": round(
                sum(row["new_unique_papers"] for row in results) / len(results),
                1,
            ) if results else 0,
            "avg_baseline_unique": round(
                sum(row["baseline"]["unique"] for row in results) / len(results),
                1,
            ) if results else 0,
            "avg_federated_unique": round(
                sum(row["federated"]["unique"] for row in results) / len(results),
                1,
            ) if results else 0,
            "avg_federated_latency": round(
                sum(row["federated"]["latency_s"] or 0 for row in results)
                / len(results),
                2,
            ) if results else 0,
        },
        "results": results,
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=1)

    print(f"\nsaved: {args.out}")
    print(json.dumps(summary["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
