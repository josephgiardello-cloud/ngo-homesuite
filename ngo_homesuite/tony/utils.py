import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List


def parse_years(years_str: str) -> List[int]:
    if not years_str:
        return []
    try:
        years = [int(y.strip()) for y in years_str.split(",") if y.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc

    current_year = datetime.now().year
    for year in years:
        if year < 1900 or year > current_year:
            raise argparse.ArgumentTypeError(f"Invalid year: {year}")
    return years


def file_exists(path: str) -> bool:
    return Path(path).exists()


def ensure_parent_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, payload: Any) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def coalesce_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))
