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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 3 <= args.samples <= 200:
        raise SystemExit("--samples must be between 3 and 200")
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "base_url": args.base_url,
        "results": [benchmark(args.base_url, endpoint, args.samples, args.timeout) for endpoint in DEFAULT_ENDPOINTS],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
