"""Check the Chenyiyun MySQL connection used by production and research scripts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def mask_sqlalchemy_url(url: str) -> str:
    split = urlsplit(url)
    netloc = split.netloc
    if "@" in netloc:
        auth, host = netloc.rsplit("@", 1)
        if ":" in auth:
            user = auth.split(":", 1)[0]
            auth = f"{user}:***"
        netloc = f"{auth}@{host}"
    query = urlencode(
        [(key, "***" if re.search("password|token|secret", key, re.I) else value) for key, value in parse_qsl(split.query, keep_blank_values=True)]
    )
    return urlunsplit((split.scheme, netloc, split.path, query, split.fragment))


def check_db_connection(prefix: str = "CHENYIYUN_DB") -> dict[str, object]:
    url = build_sqlalchemy_url(prefix)
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        score_row = conn.execute(
            text(
                """
                SELECT COUNT(*) AS row_count, MAX(trade_date) AS latest_trade_date
                FROM score_rank_daily
                """
            )
        ).mappings().first()
        price_row = conn.execute(
            text(
                """
                SELECT MAX(trade_date) AS latest_trade_date
                FROM tushare_stock.dwd_stock_daily_standard
                """
            )
        ).mappings().first()
        return {
            "sqlalchemy_url": mask_sqlalchemy_url(url),
            "current_user": conn.execute(text("SELECT CURRENT_USER()")).scalar(),
            "database": conn.execute(text("SELECT DATABASE()")).scalar(),
            "score_rank_daily_rows": int((score_row or {}).get("row_count") or 0),
            "score_rank_daily_latest_trade_date": str((score_row or {}).get("latest_trade_date") or ""),
            "dwd_stock_daily_latest_trade_date": str((price_row or {}).get("latest_trade_date") or ""),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MySQL connectivity for Chenyiyun scripts.")
    parser.add_argument("--prefix", default="CHENYIYUN_DB")
    parser.add_argument("--dry-run-url", action="store_true", help="Only print the resolved masked SQLAlchemy URL.")
    args = parser.parse_args()

    if args.dry_run_url:
        print(json.dumps({"sqlalchemy_url": mask_sqlalchemy_url(build_sqlalchemy_url(args.prefix))}, ensure_ascii=False, indent=2))
        return
    try:
        print(json.dumps(check_db_connection(args.prefix), ensure_ascii=False, indent=2, default=str))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "sqlalchemy_url": mask_sqlalchemy_url(build_sqlalchemy_url(args.prefix)),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
