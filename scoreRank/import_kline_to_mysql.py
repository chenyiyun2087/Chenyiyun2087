import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoreRank.cli.import_kline_to_mysql import main


if __name__ == "__main__":
    main()
