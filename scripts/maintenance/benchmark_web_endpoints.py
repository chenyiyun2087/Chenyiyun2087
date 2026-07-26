#!/usr/bin/env python3
"""Measure local Web endpoint latency and emit reproducible percentile evidence."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_ENDPOINTS = (
    "/sina/scores?per_page=50",
    "/sina/scores/all?per_page=50",
    "/sina/self_selected?per_page=50",
)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def benchmark(base_url: str, endpoint: str, samples: int, timeout: float) -> dict:
    timings: list[float] = []
    sizes: list[int] = []
    url = base_url.rstrip("/") + endpoint
    for sample_no in range(samples + 1):  # first request warms connection/page caches
        started = time.perf_counter()
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status = response.status
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if status != 200:
            raise RuntimeError(f"{url} returned HTTP {status}")
        if sample_no > 0:
            timings.append(elapsed_ms)
            sizes.append(len(body))
    return {
        "endpoint": endpoint,
        "samples": samples,
        "p50_ms": round(statistics.median(timings), 3),
        "p95_ms": round(percentile(timings, 0.95), 3),
        "max_ms": round(max(timings), 3),
        "mean_ms": round(statistics.mean(timings), 3),
        "response_bytes": int(statistics.median(sizes)),
    }


def benchmark_rounds(
    base_url: str,
    endpoint: str,
    samples: int,
    timeout: float,
    rounds: int,
) -> dict:
    """Run independent warmed rounds and aggregate their medians."""

    results = [
        benchmark(base_url, endpoint, samples, timeout)
        for _ in range(rounds)
    ]
    return {
        "endpoint": endpoint,
        "samples_per_round": samples,
        "rounds": rounds,
        "p50_ms": round(statistics.median(row["p50_ms"] for row in results), 3),
        "p95_ms": round(statistics.median(row["p95_ms"] for row in results), 3),
        "max_ms": round(max(row["max_ms"] for row in results), 3),
        "mean_ms": round(statistics.median(row["mean_ms"] for row in results), 3),
        "response_bytes": int(statistics.median(row["response_bytes"] for row in results)),
        "round_results": results,
    }


def compare_with_baseline(
    current: list[dict],
    baseline: list[dict],
    *,
    max_regression_pct: float = 10.0,
) -> dict:
    """Compare endpoint p95 values and fail when any regression exceeds budget."""

    previous = {str(row["endpoint"]): row for row in baseline}
    comparisons = []
    for row in current:
        endpoint = str(row["endpoint"])
        old = previous.get(endpoint)
        if old is None or float(old.get("p95_ms") or 0) <= 0:
            comparisons.append({"endpoint": endpoint, "status": "NO_BASELINE"})
            continue
        old_p95 = float(old["p95_ms"])
        new_p95 = float(row["p95_ms"])
        delta_pct = (new_p95 - old_p95) / old_p95 * 100.0
        comparisons.append(
            {
                "endpoint": endpoint,
                "baseline_p95_ms": round(old_p95, 3),
                "current_p95_ms": round(new_p95, 3),
                "delta_pct": round(delta_pct, 3),
                "status": "PASS" if delta_pct <= max_regression_pct else "FAIL",
            }
        )
    return {
        "max_regression_pct": max_regression_pct,
        "passed": all(item["status"] != "FAIL" for item in comparisons),
        "comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--max-regression-pct", type=float, default=10.0)
    args = parser.parse_args()
    if not 3 <= args.samples <= 200:
        raise SystemExit("--samples must be between 3 and 200")
    if not 1 <= args.rounds <= 20:
        raise SystemExit("--rounds must be between 1 and 20")
    results = [
        benchmark_rounds(
            args.base_url,
            endpoint,
            args.samples,
            args.timeout,
            args.rounds,
        )
        for endpoint in DEFAULT_ENDPOINTS
    ]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "base_url": args.base_url,
        "results": results,
    }
    if args.baseline:
        baseline_payload = json.loads(args.baseline.read_text(encoding="utf-8"))
        payload["comparison"] = compare_with_baseline(
            results,
            list(baseline_payload.get("results") or []),
            max_regression_pct=args.max_regression_pct,
        )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    if payload.get("comparison", {}).get("passed") is False:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
