import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.run_chenyiyun_daily import main


if __name__ == "__main__":
    main()
