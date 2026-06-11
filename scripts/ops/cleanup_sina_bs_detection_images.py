"""Weekly cleanup for Sina B/S detection image folders.

The capture job stores images under:
    sina/bs_detection/SinaAppBS/config_1/YYYYMMDD/

This maintenance script targets the previous ISO week relative to --date and
deletes those date folders only when --execute is provided. It is intended for
Friday scheduled runs after the current week's captures are no longer needed.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE_ROOT = PROJECT_ROOT / "sina" / "bs_detection" / "SinaAppBS" / "config_1"


def _normalize_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    value = str(raw).strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _previous_iso_week(target: date) -> tuple[date, date]:
    current_monday = target - timedelta(days=target.weekday())
    start = current_monday - timedelta(days=7)
    end = current_monday - timedelta(days=1)
    return start, end


def _date_from_folder_name(path: Path) -> date | None:
    name = path.name
    if len(name) != 8 or not name.isdigit():
        return None
    try:
        return datetime.strptime(name, "%Y%m%d").date()
    except ValueError:
        return None


def collect_previous_week_image_dirs(root: Path, target: date) -> tuple[date, date, list[Path]]:
    start, end = _previous_iso_week(target)
    if not root.exists():
        return start, end, []
    dirs: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        folder_date = _date_from_folder_name(child)
        if folder_date is None:
            continue
        if start <= folder_date <= end:
            dirs.append(child)
    return start, end, dirs


def _folder_file_count(path: Path) -> int:
    return sum(1 for child in path.rglob("*") if child.is_file())


def run_cleanup(args: argparse.Namespace) -> dict[str, object]:
    target_date = _normalize_date(args.date)
    root = Path(args.root).expanduser().resolve()
    start, end, dirs = collect_previous_week_image_dirs(root, target_date)
    friday_only = bool(args.friday_only)
    should_execute = bool(args.execute)
    if friday_only and target_date.weekday() != 4:
        should_execute = False

    deleted: list[dict[str, object]] = []
    planned: list[dict[str, object]] = []
    for folder in dirs:
        item = {
            "path": str(folder),
            "date": folder.name,
            "file_count": _folder_file_count(folder),
        }
        planned.append(item)
        if should_execute:
            shutil.rmtree(folder)
            deleted.append(item)

    return {
        "target_date": target_date.isoformat(),
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "root": str(root),
        "friday_only": friday_only,
        "execute_requested": bool(args.execute),
        "executed": should_execute,
        "planned_count": len(planned),
        "deleted_count": len(deleted),
        "planned": planned,
        "deleted": deleted,
        "skip_reason": "not_friday" if friday_only and target_date.weekday() != 4 else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete previous-week Sina B/S detection image folders.")
    parser.add_argument("--date", default=None, help="Reference date, YYYY-MM-DD or YYYYMMDD. Defaults to today.")
    parser.add_argument("--root", default=str(DEFAULT_IMAGE_ROOT), help="Image root containing YYYYMMDD folders.")
    parser.add_argument("--execute", action="store_true", help="Actually delete folders. Without this, only dry-runs.")
    parser.add_argument("--friday-only", action="store_true", default=True, help="Execute only when --date is Friday.")
    parser.add_argument("--allow-non-friday", action="store_false", dest="friday_only", help="Allow execution on non-Friday dates.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    print(json.dumps(run_cleanup(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
