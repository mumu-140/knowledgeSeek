#!/usr/bin/env python
"""Benchmark: single-source baseline vs federated (MOSAIC) retrieval.

Not part of core runtime — a reproducible Phase 6 driver. For each query in
tests/fixtures/federated_queries.json it runs the baseline single-source
provider (openalex by default) and MosaicFederatedProvider, then records the
metrics the integration plan (§11) requires: raw/unique candidates, new
unique papers vs baseline, source distribution, top-20 and DOI overlap,
duplicate rate, metadata completeness, latency, and per-source error counts.

Usage:
    python scripts/benchmark_federated.py [--limit 40] [--max-per-source 10]
        [--baseline openalex] [--out results.json] [--queries path]

Requires network access and the optional ``mosaic`` package for the
federated side; the baseline side needs no extra dependency.
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
    """DOIs when present; lowercase title as fallback identity."""
    keys = set()
    for r in records:
        doi = _doi(r)
        key = f"doi:{doi}" if doi else f"title:{(r.title or '').strip().lower()}"
        if key and key != "title:":
            keys.add(key)
    return keys


def _completeness(records) -> dict:
    n = len(records) or 1
    return {
        "doi_pct": round(100 * sum(1 for r in records if _doi(r)) / n, 1),
        "abstract_pct": round(100 * sum(1 for r in records if (r.abstract or "").strip()) / n, 1),
        "year_pct": round(100 * sum(1 for r in records if getattr(r.source, "publish_year", None)) / n, 1),
        "authors_pct": round(100 * sum(1 for r in records if getattr(r.names, "authors", None)) / n, 1),
    }


def _top_keys(records, n=20) -> set:
    return _fingerprint_set(records[:n])


def run_baseline(provider, query: str, limit: int) -> dict:
    started = time.time()
    try:
        result = provider.search(query, limit=limit)
        records = result.hits
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
        "metadata": getattr(result, "metadata", None) if error is None else None,
    }


def run_federated(profile: str, query: str, limit: int, max_per_source: int, email: str) -> dict:
    provider = MosaicFederatedProvider(profile=profile, max_per_source=max_per_source, email=email)
    started = time.time()
    try:
        result = provider.search(query, limit=limit)
        records = result.hits
        error = None
    except Exception as exc:
        records, error = [], f"{type(exc).__name__}: {exc}"
    wall = round(time.time() - started, 2)
    stats = provider.last_stats or {}
    raw_total = stats.get("raw_total") or 0
    unique_total = stats.get("unique") or 0
    per_source = stats.get("per_source") or {}
    return {
        "records": records,
        "raw": raw_total,
        "unique": unique_total,
        "latency_s": provider.last_latency and round(provider.last_latency, 2) or wall,
        "wall_latency_s": wall,
        "error": error,
        "source_distribution": per_source,
        "source_error_count": len(provider.last_errors or []),
        "errors": [e.split("\n")[0][:80] for e in (provider.last_errors or [])],
    }


def compare(query: dict, baseline: dict, federated: dict) -> dict:
    base_keys = _fingerprint_set(baseline["records"])
    fed_keys = _fingerprint_set(federated["records"])
    new_keys = fed_keys - base_keys
    top_overlap = _top_keys(baseline["records"]) & _top_keys(federated["records"])
    dup_rate = round(100 * (federated["raw"] - federated["unique"]) / max(1, federated["raw"]), 1)
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
            "latency_s": federated["latency_s"],
            "wall_latency_s": federated.get("wall_latency_s"),
            "source_distribution": federated["source_distribution"],
            "source_error_count": federated["source_error_count"],
            "errors": federated["errors"],
            "error": federated["error"],
            "completeness": _completeness(federated["records"]),
        },
        "new_unique_papers": len(new_keys),
        "new_unique_pct": round(100 * len(new_keys) / max(1, len(fed_keys)), 1),
        "top20_overlap_count": len(top_overlap),
        "doi_overlap_count": len(base_keys & fed_keys & {k for k in fed_keys if k.startswith("doi:")}),
        "duplicate_rate_pct": dup_rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-per-source", type=int, default=10)
    parser.add_argument("--baseline", default="openalex", choices=["openalex"])
    parser.add_argument("--out", default="federated_benchmark_results.json")
    parser.add_argument("--queries", default=str(ROOT / "tests" / "fixtures" / "federated_queries.json"))
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between queries")
    args = parser.parse_args()

    with open(args.queries, encoding="utf-8") as fh:
        spec = json.load(fh)
    queries = spec["queries"]

    baseline_provider = OpenAlexProvider(email=os.environ.get("OPENALEX_EMAIL", ""))
    email = os.environ.get("OPENALEX_EMAIL", "")

    results = []
    for q in queries:
        print(f"--- {q['id']} [{q.get('kind')}] {q['query']}", flush=True)
        base = run_baseline(baseline_provider, q["query"], args.limit)
        fed = run_federated(q.get("profile", "general"), q["query"], args.limit, args.max_per_source, email)
        row = compare(q, base, fed)
        if "expected_doi" in q:
            fed_dois = {_doi(r) for r in fed["records"]}
            row["expected_doi_found"] = q["expected_doi"].lower() in fed_dois
            base_dois = {_doi(r) for r in base["records"]}
            row["expected_doi_in_baseline"] = q["expected_doi"].lower() in base_dois
        results.append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("federated",)}, ensure_ascii=False)[:400], flush=True)
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
            "avg_new_unique": round(sum(r["new_unique_papers"] for r in results) / len(results), 1) if results else 0,
            "avg_baseline_unique": round(sum(r["baseline"]["unique"] for r in results) / len(results), 1) if results else 0,
            "avg_federated_unique": round(sum(r["federated"]["unique"] for r in results) / len(results), 1) if results else 0,
            "avg_federated_latency": round(sum(r["federated"]["latency_s"] or 0 for r in results) / len(results), 2) if results else 0,
        },
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    print(f"\nsaved: {args.out}")
    print(json.dumps(summary["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
