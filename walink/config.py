"""Loading and validating config.yaml (shared by the CLI and the SSH wrapper)."""
from __future__ import annotations

from pathlib import Path

import yaml

from . import paths
from .humanize import Pacer, Range, SAME_TYPE, SWITCH_TYPE, CLICK


class ConfigError(ValueError):
    pass


def load_config(path: Path | None = None) -> dict:
    """The parsed config; {} when the file is missing or empty. Always read as UTF-8 (chat names)."""
    p = path or paths.CONFIG
    if not p.exists():
        return {}
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise ConfigError(f"{p.name}: {e}")
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise ConfigError(f"{p.name}: top level must be a mapping")
    return cfg


def _range(timing: dict, key: str, default: Range) -> Range:
    v = timing.get(key)
    if v is None:
        return default
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ConfigError(f"timing.{key} must be [min, max] in ms, got {v!r}")
    try:
        return Range(*v)
    except ValueError as e:
        raise ConfigError(f"timing.{key}: {e}")


def make_pacer(cfg: dict) -> Pacer:
    timing = cfg.get("timing") or {}
    if not isinstance(timing, dict):
        raise ConfigError("timing must be a mapping")
    return Pacer(same=_range(timing, "same_type_ms", SAME_TYPE),
                 switch=_range(timing, "switch_type_ms", SWITCH_TYPE),
                 click=_range(timing, "click_ms", CLICK))
