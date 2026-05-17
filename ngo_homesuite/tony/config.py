import json
from copy import deepcopy
from importlib import resources
from typing import Any


with resources.files("tony").joinpath("default_config.json").open("r", encoding="utf-8") as handle:
    DEFAULT_CONFIG = json.load(handle)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | None = None) -> dict[str, Any]:
    if not path:
        return deepcopy(DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as handle:
        override = json.load(handle)
    return _deep_merge(DEFAULT_CONFIG, override)