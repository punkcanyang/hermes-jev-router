"""加载 jev-router 配置（env + ~/.hermes/jev-router.yaml + plugins.entries 设置）。密钥不进仓。"""
from __future__ import annotations

import math
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
    "auto_approve_enabled": False,
    "auto_approve_confidence": 0.80,
    "auto_approve_timeout_seconds": 8.0,
    "auto_approve_hardline_enabled": False,
}

# 有序：旧别名在前，同一字段后出现的覆盖前面的
_ENV_MAP = {
    "HERMES_JEV_ROUTING": ("routing_enabled", "bool"),
    "HERMES_JEV_TRIM": ("trim_enabled", "bool"),
    "JEV_ROUTER_MAIN_MODEL": ("primary_model", "str"),
    "JEV_ROUTER_ROUTING_ENABLED": ("routing_enabled", "bool"),
    "JEV_ROUTER_TRIM_ENABLED": ("trim_enabled", "bool"),
    "JEV_ROUTER_MIN_CONFIDENCE": ("min_confidence", "float"),
    "JEV_ROUTER_TIMEOUT": ("timeout_seconds", "float"),
    "JEV_ROUTER_PRIMARY_MODEL": ("primary_model", "str"),
    "JEV_ROUTER_CHEAP_MODEL": ("cheap_model", "str"),
    "JEV_ROUTER_COMPLEX_MODEL": ("complex_model", "str"),
    "JEV_ROUTER_TOOL_HEAVY_MODEL": ("tool_heavy_model", "str"),
    "JEV_ROUTER_LONG_CONTEXT_MODEL": ("long_context_model", "str"),
    "JEV_ROUTER_AUTO_APPROVE_ENABLED": ("auto_approve_enabled", "bool"),
    "JEV_ROUTER_AUTO_APPROVE_CONFIDENCE": ("auto_approve_confidence", "float"),
    "JEV_ROUTER_AUTO_APPROVE_TIMEOUT": ("auto_approve_timeout_seconds", "float"),
    "JEV_ROUTER_AUTO_APPROVE_HARDLINE_ENABLED": ("auto_approve_hardline_enabled", "bool"),
}

# config.example.yaml / 旧文档用的字段名 → settings 正式字段名
_ALIASES = {"enabled": "routing_enabled", "main_model": "primary_model"}

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def as_bool(val: Any, default: bool) -> bool:
    """严格布尔：YAML 里的字符串 "false" 不能被 bool() 当成 True；无法识别 → default。"""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return val != 0
    if isinstance(val, str):
        s = val.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return default


def _as_float(val: Any, default: float) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _coerce(val: str, kind: str) -> Any:
    if kind == "bool":
        # 拼错的环境变量按 False 处理：所有开关的 False 都是安全侧
        return val.strip().lower() in _TRUE
    if kind == "float":
        return float(val)
    if kind == "int":
        return int(val)
    return val


def _normalize_block(data: Any) -> dict[str, Any]:
    """接受扁平字段或 jev_router: 块；应用字段别名，丢弃 context:/plugins: 等宿主块。"""
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("jev_router"), dict):
        data = data["jev_router"]
    out = {k: v for k, v in data.items() if k not in ("jev_router", "context", "plugins")}
    for alias, canonical in _ALIASES.items():
        if alias in out:
            val = out.pop(alias)
            out.setdefault(canonical, val)
    return out


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
    cfg.update(_normalize_block(_load_yaml(home / "jev-router.yaml")))
    hermes_cfg = _load_yaml(home / "config.yaml")
    # Hermes config.yaml → 顶层 jev_router: 块，再 plugins.entries.jev-router.settings
    cfg.update(_normalize_block(hermes_cfg.get("jev_router")))
    entries = ((hermes_cfg.get("plugins") or {}).get("entries") or {}).get("jev-router") or {}
    settings = entries.get("settings") if isinstance(entries, dict) else None
    cfg.update(_normalize_block(settings))
    cfg.update(_normalize_block(plugin_settings))
    for env_key, (cfg_key, kind) in _ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw != "":
            try:
                cfg[cfg_key] = _coerce(raw, kind)
            except Exception:
                pass
    for key, default in DEFAULTS.items():
        if isinstance(default, bool):
            cfg[key] = as_bool(cfg.get(key), default)
        elif isinstance(default, float):
            cfg[key] = _as_float(cfg.get(key), default)
        elif isinstance(default, int):
            cfg[key] = int(_as_float(cfg.get(key), float(default)))
    cfg["event_log"] = str(Path(str(cfg.get("event_log") or DEFAULTS["event_log"])).expanduser())
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
