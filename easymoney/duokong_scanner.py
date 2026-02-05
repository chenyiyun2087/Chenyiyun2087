"""Scan multi/short sentiment (多空看盘) from Eastmoney Guba pages."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import re
import urllib.error
import urllib.request


@dataclass(frozen=True)
class DuokongSnapshot:
    """Structured snapshot of the multi/short sentiment widget."""

    code: str
    bulls_percent: float
    bears_percent: float
    bulls_votes: int | None = None
    bears_votes: int | None = None
    price: float | None = None
    change_percent: float | None = None
    snapshot_time: dt.datetime | None = None
    source_url: str | None = None


_MULTI_SHORT_RE = re.compile(
    r"多空看盘.*?([0-9]+\\.?[0-9]*)%.*?([0-9]+\\.?[0-9]*)%",
    re.DOTALL,
)
_BULLS_VOTES_RE = re.compile(r"看涨[^0-9]*([0-9,]+)")
_BEARS_VOTES_RE = re.compile(r"看跌[^0-9]*([0-9,]+)")
_PRICE_PATTERNS = [
    re.compile(r"最新价[:：]?\s*([0-9]+\\.?[0-9]*)"),
    re.compile(r"\"f2\"\\s*:\\s*([0-9]+\\.?[0-9]*)"),
]
_CHANGE_PATTERNS = [
    re.compile(r"涨跌幅[:：]?\s*([+-]?[0-9]+\\.?[0-9]*)%"),
    re.compile(r"\"f3\"\\s*:\\s*([+-]?[0-9]+\\.?[0-9]*)"),
]


def _build_url(code: str) -> str:
    return f"https://guba.eastmoney.com/list,{code}.html"


def _fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法获取页面: {exc}") from exc


def _parse_votes(pattern: re.Pattern[str], html: str) -> int | None:
    match = pattern.search(html)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _parse_first_float(patterns: list[re.Pattern[str]], html: str) -> float | None:
    for pattern in patterns:
        match = pattern.search(html)
        if match:
            return float(match.group(1))
    return None


def fetch_duokong_snapshot(code: str, html: str | None = None) -> DuokongSnapshot:
    """Fetch and parse the multi/short sentiment widget for a stock code."""

    url = _build_url(code)
    page_html = html if html is not None else _fetch_html(url)
    match = _MULTI_SHORT_RE.search(page_html)
    if not match:
        raise ValueError("未找到多空看盘数据，请检查页面结构是否变化。")
    bulls_percent = float(match.group(1))
    bears_percent = float(match.group(2))
    bulls_votes = _parse_votes(_BULLS_VOTES_RE, page_html)
    bears_votes = _parse_votes(_BEARS_VOTES_RE, page_html)
    price = _parse_first_float(_PRICE_PATTERNS, page_html)
    change_percent = _parse_first_float(_CHANGE_PATTERNS, page_html)
    return DuokongSnapshot(
        code=code,
        bulls_percent=bulls_percent,
        bears_percent=bears_percent,
        bulls_votes=bulls_votes,
        bears_votes=bears_votes,
        price=price,
        change_percent=change_percent,
        snapshot_time=dt.datetime.now(),
        source_url=url,
    )


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="扫描东方财富股吧多空看盘数据")
    parser.add_argument("code", help="股票代码，例如 688158")
    args = parser.parse_args()
    snapshot = fetch_duokong_snapshot(args.code)
    print(json.dumps(snapshot.__dict__, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
