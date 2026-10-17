"""Read-only check of the dedicated queue worker's database lease."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ops.task_worker_launcher import load_env, ENV_FILE
from scoreRank.core.db_runtime import connect_pymysql


def main():
    load_env(ENV_FILE)
    conn = connect_pymysql()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT IS_USED_LOCK('chenyiyun.task_queue_worker') AS owner")
            owner = (cursor.fetchone() or {}).get("owner")
        if owner is None:
            raise SystemExit("FAIL: worker database lease is not held")
        print("PASS: worker database lease is held")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
