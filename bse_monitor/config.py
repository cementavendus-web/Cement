"""Configuration loading.

Values come from ``configs/config.yaml`` and can be overridden per-key by an
environment variable named ``BSE_<SECTION>_<KEY>`` (upper-cased, nested keys
joined by underscores). That keeps secrets out of the repository while leaving
a single readable source of truth for everything else.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import yaml

CONFIG_DIR = Path(__file__).resolve().parent / "configs"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.yaml"
KEYWORDS_PATH = CONFIG_DIR / "keywords.yaml"
INVESTORS_PATH = CONFIG_DIR / "investors.yaml"

ENV_PREFIX = "BSE_"


def _coerce(raw: str) -> Any:
    """Turn an environment string into the closest native type."""
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if "," in raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw


def _walk(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}_{key}" if prefix else str(key)
            yield from _walk(value, path)
    else:
        yield prefix, node


def _set_path(tree: Dict[str, Any], dotted: list[str], value: Any) -> None:
    cursor = tree
    for part in dotted[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[dotted[-1]] = value


def _env_key_map(tree: Dict[str, Any]) -> Dict[str, list[str]]:
    """Map ``BSE_ALERTS_SLACK_WEBHOOK_URL`` -> ``['alerts','slack','webhook_url']``."""
    mapping: Dict[str, list[str]] = {}

    def recurse(node: Any, path: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                recurse(value, path + [str(key)])
        if path:
            mapping[ENV_PREFIX + "_".join(path).upper()] = path

    recurse(tree, [])
    return mapping


def apply_env_overrides(tree: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(tree)
    mapping = _env_key_map(merged)
    for env_name, path in mapping.items():
        if env_name in os.environ:
            _set_path(merged, path, _coerce(os.environ[env_name]))
    # DATABASE_URL is such a common convention that it is honoured directly.
    if os.getenv("DATABASE_URL"):
        _set_path(merged, ["database", "url"], os.environ["DATABASE_URL"])
    return merged


@dataclass
class Config:
    """Dict-backed configuration with dotted-path access."""

    data: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None

    @classmethod
    def load(cls, path: Optional[Path | str] = None) -> "Config":
        cfg_path = Path(path or os.getenv("BSE_CONFIG_FILE") or DEFAULT_CONFIG_PATH)
        with open(cfg_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls(data=apply_env_overrides(raw), path=cfg_path)

    def get(self, dotted: str, default: Any = None) -> Any:
        cursor: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def section(self, name: str) -> Dict[str, Any]:
        value = self.get(name, {})
        return value if isinstance(value, dict) else {}

    def resolve_path(self, dotted: str, default: str) -> Path:
        """Resolve a configured directory, creating it on first use."""
        raw = self.get(dotted, default)
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target


def load_keywords(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or KEYWORDS_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_investors(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or INVESTORS_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
