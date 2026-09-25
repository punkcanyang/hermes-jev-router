"""Jev 自动同意策略：高置信 approve → 宿主 once；其余 needs_human。

默认关闭。不复用 YOLO／approvals.mode:off。禁止 session／always 缓存。
硬禁第二开关默认关；开启前须一次风险确认（ack 文件）。
用户 approvals.deny 由宿主在调用策略之前拦截；本插件看不到也不改变它。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Mapping

try:
    from .events import emit, scrub
    from .settings import as_bool, load_settings
    from .timeouts import FuturesTimeout, call_with_timeout
except ImportError:  # pragma: no cover
    from events import emit, scrub
    from settings import as_bool, load_settings
    from timeouts import FuturesTimeout, call_with_timeout

logger = logging.getLogger("hermes.plugins.jev_router.auto_approve")

NEEDS_HUMAN = "needs_human"
ONCE = "once"
SUPPORTED_SCHEMA_VERSION = 1

_VALID = frozenset({"approve", "deny", "unsure"})
# 配置再低也不会按低于此值的置信度自动同意
MIN_CONFIDENCE_FLOOR = 0.5
# 给宿主留出余量：宿主自己的策略超时到点前，本插件先放弃并交回人审
_HOST_TIMEOUT_MARGIN_S = 0.5
_RISK_TEXT = (
    "Hardline exists to unconditionally block catastrophic commands. "
    "Enabling Jev auto-approve for hardline may allow a high-confidence "
    "once approval. When unsure, Hermes still asks a human / fails closed. "
    "User approvals.deny rules remain absolute."
)
_RISK_HASH = hashlib.sha256(_RISK_TEXT.encode("utf-8")).hexdigest()


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _ack_path() -> Path:
    return _hermes_home() / "jev-router" / "hardline-ack.json"


def risk_text() -> str:
    return _RISK_TEXT


def hardline_risk_ack_present() -> bool:
    """ack 必须针对当前风险文案；文案变了须重新确认。"""
    path = _ack_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    return data.get("acknowledged") is True and data.get("ack_hash") == _RISK_HASH


def write_hardline_risk_ack(*, acknowledged_by: str = "operator") -> Path:
    path = _ack_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "acknowledged": True,
        "acknowledged_by": str(acknowledged_by)[:64],
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "risk_text": _RISK_TEXT,
        "ack_hash": _RISK_HASH,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def clear_hardline_risk_ack() -> None:
    path = _ack_path()
    if path.is_file():
        path.unlink()


def _mock_response(kind: str, timeout: float) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind == "timeout":
        # 迟到的 approve 必须被丢弃：睡得比本次预算更久
        time.sleep(max(float(timeout), 0.0) + 1.0)
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


def ask_jev_for_approval(
    payload: dict[str, Any],
    cfg: dict[str, Any],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    mock = os.environ.get("JEV_ROUTER_AUTO_APPROVE_MOCK", "").strip()
    if timeout is None:
        timeout = float(cfg.get("auto_approve_timeout_seconds") or cfg.get("timeout_seconds") or 8.0)
    if mock:
        def _call() -> dict[str, Any]:
            return _mock_response(mock, timeout)
    else:
        if not os.environ.get("TYPESAFE_API_KEY"):
            return {"decision": "unsure", "confidence": 0.0, "reason_code": "missing_typesafe_key"}

        def _call() -> dict[str, Any]:
            return _ask_jev_live(payload, cfg)

    return call_with_timeout(_call, timeout)


def _normalize_jev_result(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    decision = str(raw.get("decision") or "").strip().lower()
    if decision not in _VALID:
        return None
    conf_raw = raw.get("confidence")
    if isinstance(conf_raw, bool) or not isinstance(conf_raw, (int, float, str)):
        return None
    try:
        confidence = float(conf_raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        return None
    reason = str(raw.get("reason_code") or "unspecified")[:64]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", reason):
        reason = "invalid_reason"
    return {"decision": decision, "confidence": confidence, "reason_code": reason}


def map_jev_to_host(result: dict[str, Any] | None, *, min_confidence: float) -> str:
    if not result:
        return NEEDS_HUMAN
    threshold = max(float(min_confidence), MIN_CONFIDENCE_FLOOR)
    if result["decision"] == "approve" and float(result["confidence"]) >= threshold:
        return ONCE
    return NEEDS_HUMAN


def _effective_timeout(cfg: dict[str, Any], request: Any) -> float:
    timeout = float(cfg.get("auto_approve_timeout_seconds") or cfg.get("timeout_seconds") or 8.0)
    try:
        host_budget = float(getattr(request, "timeout_seconds", 0) or 0)
    except (TypeError, ValueError):
        host_budget = 0.0
    if math.isfinite(host_budget) and host_budget > 0:
        timeout = min(timeout, max(host_budget - _HOST_TIMEOUT_MARGIN_S, 0.1))
    return max(timeout, 0.1)


def build_policy_callback(plugin_settings: dict[str, Any] | None = None):
    def policy_callback(request) -> dict[str, str]:
        try:
            return _decide(request, plugin_settings)
        except Exception:
            logger.warning("auto_approve policy failed; handing to human", exc_info=True)
            return {"decision": NEEDS_HUMAN}

    return policy_callback


def _decide(request: Any, plugin_settings: dict[str, Any] | None) -> dict[str, str]:
    started = time.monotonic()
    cfg = load_settings(plugin_settings)
    event_log = str(cfg.get("event_log") or str(_hermes_home() / "jev-router" / "events.jsonl"))
    enabled = as_bool(cfg.get("auto_approve_enabled"), False)
    hardline_enabled = as_bool(cfg.get("auto_approve_hardline_enabled"), False)
    min_conf = float(cfg.get("auto_approve_confidence") or 0.80)
    mock = os.environ.get("JEV_ROUTER_AUTO_APPROVE_MOCK", "").strip()

    # 缺字段／非 False → 按硬禁处理（更严）
    is_hardline = getattr(request, "is_hardline", None) is not False
    schema_version = getattr(request, "schema_version", None)
    surface = str(getattr(request, "surface", "") or "")
    pattern_key = str(getattr(request, "pattern_key", "") or "")
    pattern_keys = [str(k) for k in list(getattr(request, "pattern_keys", ()) or [])[:8]]
    digest = str(getattr(request, "digest", "") or "")
    command = scrub(getattr(request, "command", "") or "")
    description = scrub(getattr(request, "description", "") or "")

    def _log(host_decision: str, *, decided_by: str, **extra: Any) -> None:
        try:
            emit(
                event_log,
                "auto_approve",
                surface=surface,
                pattern=pattern_key,
                pattern_keys=pattern_keys,
                digest=digest,
                decision=host_decision,
                decided_by=decided_by,
                is_hardline=is_hardline,
                latency_ms=int((time.monotonic() - started) * 1000),
                **extra,
            )
        except Exception:
            logger.debug("auto_approve emit failed", exc_info=True)

    def _skip(reason_code: str) -> dict[str, str]:
        _log(NEEDS_HUMAN, decided_by="none", skipped=True, reason_code=reason_code)
        return {"decision": NEEDS_HUMAN}

    if not enabled:
        return _skip("flag_off")

    if schema_version != SUPPORTED_SCHEMA_VERSION:
        return _skip("unsupported_request_schema")

    if is_hardline:
        if not hardline_enabled:
            return _skip("hardline_flag_off")
        if not hardline_risk_ack_present():
            return _skip("hardline_ack_missing")
        if mock:
            return _skip("hardline_mock_refused")

    decided_by = "mock" if mock else "jev"
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
        raw = ask_jev_for_approval(payload, cfg, timeout=_effective_timeout(cfg, request))
    except FuturesTimeout:
        _log(NEEDS_HUMAN, decided_by=decided_by, error_class="timeout")
        return {"decision": NEEDS_HUMAN}
    except Exception as exc:
        _log(NEEDS_HUMAN, decided_by=decided_by, error_class=type(exc).__name__)
        return {"decision": NEEDS_HUMAN}

    normalized = _normalize_jev_result(raw)
    if normalized is None:
        _log(NEEDS_HUMAN, decided_by=decided_by, error_class="malformed")
        return {"decision": NEEDS_HUMAN}
    host = map_jev_to_host(normalized, min_confidence=min_conf)
    _log(
        host,
        decided_by=decided_by,
        jev_decision=normalized["decision"],
        confidence=normalized["confidence"],
        reason_code=normalized["reason_code"],
        threshold=max(min_conf, MIN_CONFIDENCE_FLOOR),
    )
    return {"decision": host}


def try_register_approval_policy(ctx, plugin_settings: dict[str, Any] | None = None) -> bool:
    register = getattr(ctx, "register_approval_policy", None)
    if not callable(register):
        logger.info(
            "Host lacks register_approval_policy; Jev auto-approve inactive "
            "(needs Hermes approval-policy extension)"
        )
        return False
    if os.environ.get("JEV_ROUTER_AUTO_APPROVE_MOCK", "").strip():
        logger.warning(
            "JEV_ROUTER_AUTO_APPROVE_MOCK is set: approvals are decided by a canned mock, "
            "not Jev. Unset it outside demos."
        )
    try:
        register("jev", build_policy_callback(plugin_settings))
        logger.info("Registered approval policy 'jev'")
        return True
    except Exception as exc:
        logger.warning("register_approval_policy failed: %s", exc)
        return False
