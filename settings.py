"""加载 jev-router 配置（env + ~/.hermes/jev-router.yaml + plugins.entries 设置）。密钥不进仓。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

DEFAULTS: dict[str, Any] = {
    "routing_enabled": True,
    "trim_enabled": True,
    "min_confidence": 0.55,
    "timeout_seconds": 8.0,
    "jev_model": "jev-latest",
    "primary_model": "deepseek-flash",
    "cheap_model": "deepseek-flash",
    "complex_model": "deepseek-chat",
    "tool_heavy_model": "deepseek-chat",
    "long_context_model": "deepseek-chat",
    "keep_last_n_turns": 6,
    "drop_old_tool_noise": True,
    "event_log": "~/.hermes/jev-router/events.jsonl",
}

_ENV_MAP = {
    "JEV_ROUTER_ROUTING_ENABLED": ("routing_enabled", "bool"),
    "JEV_ROUTER_TRIM_ENABLED": ("trim_enabled", "bool"),
    "JEV_ROUTER_MIN_CONFIDENCE": ("min_confidence", "float"),
    "JEV_ROUTER_TIMEOUT": ("timeout_seconds", "float"),
    "JEV_ROUTER_PRIMARY_MODEL": ("primary_model", "str"),
    "JEV_ROUTER_CHEAP_MODEL": ("cheap_model", "str"),
    "JEV_ROUTER_COMPLEX_MODEL": ("complex_model", "str"),
}


def _coerce(val: str, kind: str) -> Any:
    if kind == "bool":
        return val.strip().lower() in ("1", "true", "yes", "on")
    if kind == "float":
        return float(val)
    if kind == "int":
        return int(val)
    return val


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file() or yaml is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def load_settings(plugin_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    home = _hermes_home()
    cfg.update(_load_yaml(home / "jev-router.yaml"))
    # Hermes config.yaml → plugins.entries.jev-router.settings
    hermes_cfg = _load_yaml(home / "config.yaml")
    entries = ((hermes_cfg.get("plugins") or {}).get("entries") or {}).get("jev-router") or {}
    settings = entries.get("settings") if isinstance(entries, dict) else None
    if isinstance(settings, dict):
        cfg.update(settings)
    if plugin_settings:
        cfg.update(plugin_settings)
    for env_key, (cfg_key, kind) in _ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            try:
                cfg[cfg_key] = _coerce(raw, kind)
            except Exception:
                pass
    cfg["event_log"] = str(Path(str(cfg["event_log"])).expanduser())
    return cfg


def candidate_labels(cfg: dict[str, Any]) -> dict[str, str]:
    """label → model id（供 Jev Choice criteria）。"""
    return {
        "cheap": str(cfg["cheap_model"]),
        "primary": str(cfg["primary_model"]),
        "complex": str(cfg["complex_model"]),
        "tool_heavy": str(cfg["tool_heavy_model"]),
        "long_context": str(cfg["long_context_model"]),
    }
