"""Feature flags: HERMES_JEV_ROUTING / HERMES_JEV_TRIM + config jev_router.*."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
try:
    import yaml
except Exception:
    yaml = None
DEFAULTS = {
    "enabled": True, "trim_enabled": True, "min_confidence": 0.55, "timeout_seconds": 8.0,
    "jev_model": "jev-latest", "main_model": "deepseek-flash", "cheap_model": "deepseek-flash",
    "keep_last_n_turns": 6, "drop_old_tool_noise": True, "event_log": "~/.hermes/jev-router/events.jsonl",
}
def _hermes_home():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
def _load_yaml(path):
    if not path.is_file() or yaml is None: return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
def _as_bool(val, default=True):
    if val is None: return default
    if isinstance(val, bool): return val
    return str(val).strip().lower() in ("1","true","yes","on")
def load_jev_config(plugin_settings=None):
    cfg = dict(DEFAULTS)
    home = _hermes_home()
    file_cfg = _load_yaml(home / "jev-router.yaml")
    if isinstance(file_cfg.get("jev_router"), dict): cfg.update(file_cfg["jev_router"])
    else: cfg.update({k:v for k,v in file_cfg.items() if k != "jev_router"})
    hermes_cfg = _load_yaml(home / "config.yaml")
    top = hermes_cfg.get("jev_router")
    if isinstance(top, dict): cfg.update(top)
    entries = ((hermes_cfg.get("plugins") or {}).get("entries") or {}).get("jev-router") or {}
    settings = entries.get("settings") if isinstance(entries, dict) else None
    if isinstance(settings, dict):
        mapped = dict(settings)
        if "routing_enabled" in mapped and "enabled" not in mapped: mapped["enabled"] = mapped.pop("routing_enabled")
        if "primary_model" in mapped and "main_model" not in mapped: mapped["main_model"] = mapped["primary_model"]
        cfg.update(mapped)
    model_block = hermes_cfg.get("model") or {}
    hermes_main = model_block.get("default") or model_block.get("model")
    if hermes_main and cfg.get("main_model") == DEFAULTS["main_model"]:
        cfg["main_model"] = str(hermes_main)
    if plugin_settings:
        mapped = dict(plugin_settings)
        if "routing_enabled" in mapped and "enabled" not in mapped: mapped["enabled"] = mapped["routing_enabled"]
        if "primary_model" in mapped and "main_model" not in mapped: mapped["main_model"] = mapped["primary_model"]
        cfg.update(mapped)
    for ek, ck, cast in (
        ("JEV_ROUTER_MIN_CONFIDENCE","min_confidence",float),
        ("JEV_ROUTER_TIMEOUT","timeout_seconds",float),
    ):
        if os.environ.get(ek):
            try: cfg[ck] = cast(os.environ[ek])
            except ValueError: pass
    if os.environ.get("JEV_ROUTER_MAIN_MODEL") or os.environ.get("JEV_ROUTER_PRIMARY_MODEL"):
        cfg["main_model"] = os.environ.get("JEV_ROUTER_MAIN_MODEL") or os.environ.get("JEV_ROUTER_PRIMARY_MODEL")
    if os.environ.get("JEV_ROUTER_CHEAP_MODEL"):
        cfg["cheap_model"] = os.environ["JEV_ROUTER_CHEAP_MODEL"]
    cfg["event_log"] = str(Path(str(cfg["event_log"])).expanduser())
    cfg["enabled"] = _as_bool(cfg.get("enabled"), True)
    cfg["trim_enabled"] = _as_bool(cfg.get("trim_enabled"), True)
    return cfg
def routing_enabled(cfg=None):
    env = os.environ.get("HERMES_JEV_ROUTING")
    if env is not None and env != "":
        if str(env).strip().lower() in ("0","false","no","off"): return False
        if str(env).strip().lower() in ("1","true","yes","on"): return True
    legacy = os.environ.get("JEV_ROUTER_ROUTING_ENABLED")
    if legacy is not None and legacy != "": return _as_bool(legacy, True)
    return _as_bool((cfg if cfg is not None else load_jev_config()).get("enabled"), True)
def trim_enabled(cfg=None):
    env = os.environ.get("HERMES_JEV_TRIM")
    if env is not None and env != "":
        if str(env).strip().lower() in ("0","false","no","off"): return False
        if str(env).strip().lower() in ("1","true","yes","on"): return True
    legacy = os.environ.get("JEV_ROUTER_TRIM_ENABLED")
    if legacy is not None and legacy != "": return _as_bool(legacy, True)
    return _as_bool((cfg if cfg is not None else load_jev_config()).get("trim_enabled"), True)
