"""Jev 自动同意策略：高置信 approve → 宿主 once；其余 needs_human。

默认关闭。不复用 YOLO／approvals.mode:off。禁止 session／always 缓存。
硬禁第二开关默认关；开启前须一次风险确认（ack 文件）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any, Mapping

try:
    from .events import emit
    from .settings import load_settings
except ImportError:  # pragma: no cover
    from events import emit
    from settings import load_settings

logger = logging.getLogger("hermes.plugins.jev_router.auto_approve")

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization|bearer)\s*[=:]\s*\S+"
)
_VALID = frozenset({"approve", "deny", "unsure"})
_RISK_TEXT = (
    "Hardline exists to unconditionally block catastrophic commands. "
    "Enabling Jev auto-approve for hardline may allow a high-confidence "
    "once approval. When unsure, Hermes still asks a human / fails closed. "
    "User approvals.deny rules remain absolute."
)


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _ack_path() -> Path:
    return _hermes_home() / "jev-router" / "hardline-ack.json"


def hardline_risk_ack_present() -> bool:
    path = _ack_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("acknowledged") is True and data.get("ack_hash"))


def write_hardline_risk_ack(*, acknowledged_by: str = "operator") -> Path:
    path = _ack_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "acknowledged": True,
        "acknowledged_by": acknowledged_by,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "risk_text": _RISK_TEXT,
        "ack_hash": hashlib.sha256(_RISK_TEXT.encode("utf-8")).hexdigest(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def clear_hardline_risk_ack() -> None:
    path = _ack_path()
    if path.is_file():
        path.unlink()


def _scrub(text: str) -> str:
    s = str(text or "")
    s = _SECRET_RE.sub(r"\1=[REDACTED]", s)
    s = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", s)
    s = re.sub(r"(?i)typesafe_api_key\s*[=:]\s*\S+", "TYPESAFE_API_KEY=[REDACTED]", s)
    s = re.sub(r"(?i)\b(sk-[A-Za-z0-9]{8,}|SECRET\d+)\b", "[REDACTED]", s)
    return s[:400]


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _mock_response(kind: str) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind == "timeout":
        time.sleep(5)
        return {"decision": "approve", "confidence": 0.99, "reason_code": "mock_late"}
    if kind == "error":
        raise RuntimeError("mock_jev_error")
    table = {
        "approve": {"decision": "approve", "confidence": 0.95, "reason_code": "mock_approve"},
        "deny": {"decision": "deny", "confidence": 0.95, "reason_code": "mock_deny"},
        "unsure": {"decision": "unsure", "confidence": 0.50, "reason_code": "mock_unsure"},
        "low_confidence": {"decision": "approve", "confidence": 0.40, "reason_code": "mock_low"},
        "malformed": {"decision": "APPROVE_YES", "confidence": "high", "reason_code": "x" * 200},
    }
    if kind in table:
        return dict(table[kind])
    return {"decision": "unsure", "confidence": 0.0, "reason_code": "mock_default_unsure"}


def _ask_jev_live(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        from .routing import _ensure_typesafe_path
    except ImportError:
        from routing import _ensure_typesafe_path
    _ensure_typesafe_path()
    from typesafe_sdk import Choice, TypeSafeClient

    state = (
        f"surface: {payload.get('surface')}\n"
        f"is_hardline: {payload.get('is_hardline')}\n"
        f"pattern_key: {payload.get('pattern_key')}\n"
        f"pattern_keys: {payload.get('pattern_keys')}\n"
        f"description: {payload.get('description')}\n"
        f"command_redacted: {payload.get('command')}\n"
        f"digest: {payload.get('digest')}\n"
        "Decide whether Hermes should auto-approve THIS ONE request.\n"
        "Prefer deny/unsure when uncertain. Never approve destructive hardline lightly."
    )
    questions = {
        "verdict": Choice(
            instructions=(
                "You are a safety co-pilot for Hermes Agent approvals. "
                "Return approve only when the redacted command is clearly safe to run once. "
                "Return deny when it looks harmful. Return unsure when ambiguous."
            ),
            criteria={
                "approve": "Safe to auto-approve once at high confidence",
                "deny": "Should not auto-approve; looks risky or disallowed",
                "unsure": "Not enough signal; hand to human",
            },
        )
    }
    model = str(cfg.get("jev_model") or "jev-latest")
    with TypeSafeClient(model=model) as client:
        result = client.system_one(state=state, questions=questions, model=model)
    ans = result.choices.get("verdict")
    decision = (ans.choice if ans else None) or "unsure"
    if decision not in _VALID:
        decision = "unsure"
    confidence = float(ans.confidence) if ans and ans.confidence is not None else 0.0
    return {"decision": decision, "confidence": confidence, "reason_code": f"jev_{decision}"}


def ask_jev_for_approval(payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    mock = os.environ.get("JEV_ROUTER_AUTO_APPROVE_MOCK", "").strip()
    timeout = float(cfg.get("auto_approve_timeout_seconds") or cfg.get("timeout_seconds") or 8.0)
    if mock:
        def _call() -> dict[str, Any]:
            return _mock_response(mock)
    else:
        if not os.environ.get("TYPESAFE_API_KEY"):
            return {"decision": "unsure", "confidence": 0.0, "reason_code": "missing_typesafe_key"}

        def _call() -> dict[str, Any]:
            return _ask_jev_live(payload, cfg)

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(_call)
        return fut.result(timeout=timeout)


def _normalize_jev_result(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    decision = str(raw.get("decision") or "").strip().lower()
    if decision not in _VALID:
        return None
    try:
        confidence = float(raw.get("confidence"))
    except Exception:
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    reason = str(raw.get("reason_code") or "unspecified")[:64]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", reason):
        reason = "invalid_reason"
    return {"decision": decision, "confidence": confidence, "reason_code": reason}


def map_jev_to_host(result: dict[str, Any] | None, *, min_confidence: float) -> str:
    if not result:
        return "needs_human"
    if result["decision"] == "approve" and float(result["confidence"]) >= float(min_confidence):
        return "once"
    return "needs_human"


def build_policy_callback(plugin_settings: dict[str, Any] | None = None):
    def policy_callback(request) -> dict[str, str]:
        started = time.monotonic()
        cfg = load_settings(plugin_settings)
        event_log = str(cfg.get("event_log") or str(_hermes_home() / "jev-router" / "events.jsonl"))
        enabled = bool(cfg.get("auto_approve_enabled"))
        hardline_enabled = bool(cfg.get("auto_approve_hardline_enabled"))
        min_conf = float(cfg.get("auto_approve_confidence") or 0.80)

        env_enabled = _env_bool("JEV_ROUTER_AUTO_APPROVE_ENABLED")
        if env_enabled is not None:
            enabled = env_enabled
        env_hard = _env_bool("JEV_ROUTER_AUTO_APPROVE_HARDLINE_ENABLED")
        if env_hard is not None:
            hardline_enabled = env_hard

        is_hardline = bool(getattr(request, "is_hardline", False))
        surface = str(getattr(request, "surface", "") or "")
        pattern_key = str(getattr(request, "pattern_key", "") or "")
        pattern_keys = list(getattr(request, "pattern_keys", ()) or [])
        digest = str(getattr(request, "digest", "") or "")
        command = _scrub(getattr(request, "command", "") or "")
        description = _scrub(getattr(request, "description", "") or "")

        def _log(host_decision: str, **extra: Any) -> None:
            try:
                emit(
                    event_log,
                    "auto_approve",
                    surface=surface,
                    pattern=pattern_key,
                    pattern_keys=pattern_keys[:8],
                    digest=digest,
                    decision=host_decision,
                    decided_by="jev",
                    is_hardline=is_hardline,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    **extra,
                )
            except Exception:
                logger.debug("auto_approve emit failed", exc_info=True)

        if not enabled:
            _log("needs_human", skipped=True, reason_code="flag_off")
            return {"decision": "needs_human"}

        if is_hardline:
            if not hardline_enabled:
                _log("needs_human", skipped=True, reason_code="hardline_flag_off")
                return {"decision": "needs_human"}
            if not hardline_risk_ack_present():
                _log("needs_human", skipped=True, reason_code="hardline_ack_missing")
                return {"decision": "needs_human"}

        payload = {
            "surface": surface,
            "is_hardline": is_hardline,
            "pattern_key": pattern_key,
            "pattern_keys": pattern_keys,
            "description": description,
            "command": command,
            "digest": digest,
        }
        try:
            raw = ask_jev_for_approval(payload, cfg)
            normalized = _normalize_jev_result(raw)
            if normalized is None:
                _log("needs_human", error_class="malformed")
                return {"decision": "needs_human"}
            host = map_jev_to_host(normalized, min_confidence=min_conf)
            _log(
                host,
                jev_decision=normalized["decision"],
                confidence=normalized["confidence"],
                reason_code=normalized["reason_code"],
                threshold=min_conf,
            )
            return {"decision": host}
        except FuturesTimeout:
            _log("needs_human", error_class="timeout")
            return {"decision": "needs_human"}
        except Exception as exc:
            _log("needs_human", error_class=type(exc).__name__)
            return {"decision": "needs_human"}

    return policy_callback


def try_register_approval_policy(ctx, plugin_settings: dict[str, Any] | None = None) -> bool:
    register = getattr(ctx, "register_approval_policy", None)
    if not callable(register):
        logger.warning(
            "Host lacks register_approval_policy; Jev auto-approve inactive "
            "(needs Hermes approval-policy extension)"
        )
        return False
    try:
        register("jev", build_policy_callback(plugin_settings))
        logger.info("Registered approval policy 'jev'")
        return True
    except Exception as exc:
        logger.warning("register_approval_policy failed: %s", exc)
        return False
