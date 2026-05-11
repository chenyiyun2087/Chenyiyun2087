import sys
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def main() -> None:
    engine = create_engine(build_sqlalchemy_url())

    with engine.connect() as conn:
        try:
            print("--- BS Table Create ---")
            res1 = conn.execute(text("SHOW CREATE TABLE bs_detection_results")).fetchone()
            print(res1[1] if res1 else "Not found")

            print("\n--- Stock Table Create ---")
            res2 = conn.execute(text("SHOW CREATE TABLE tushare_stock.dwd_stock_daily_standard")).fetchone()
            print(res2[1] if res2 else "Not found")

        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
