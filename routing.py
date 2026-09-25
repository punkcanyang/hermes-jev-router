"""Jev（TypeSafe 直连）模型路由：Choice + confidence；失败／低置信 → 主模型。"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

try:
    from .settings import candidate_labels, load_settings
    from .events import emit
except ImportError:
    from settings import candidate_labels, load_settings
    from events import emit

# 回合级缓存：pre_llm_call 写，llm_request middleware 读
_TURN_DECISIONS: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _ensure_typesafe_path() -> None:
    venv_site = Path("/workspace/tools/typesafe-venv/lib/python3.13/site-packages")
    if venv_site.is_dir():
        p = str(venv_site)
        if p not in sys.path:
            sys.path.insert(0, p)


def _heuristic_route(text: str, cfg: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """无密钥／超时兜底：规则粗分，仍带 confidence。"""
    t = (text or "").lower()
    labels = candidate_labels(cfg)
    toolish = any(k in t for k in ("tool", "shell", "终端", "命令", "文件", "patch", "browser", "grep", "跑评测"))
    longish = len(text or "") > 1200 or int(meta.get("approx_tokens") or 0) > 8000 or meta.get("bucket") == "long-context"
    complexish = any(k in t for k in ("设计", "架构", "推理", "证明", "多步", "refactor", "architecture", "why", "对比"))
    if meta.get("bucket") == "tool-heavy" or toolish:
        label = "tool_heavy"
    elif meta.get("bucket") == "long-context" or longish:
        label = "long_context"
    elif meta.get("bucket") == "complex" or complexish:
        label = "complex"
    elif meta.get("bucket") == "simple":
        label = "cheap"
    else:
        label = "primary"
    model = labels.get(label) or cfg["primary_model"]
    return {
        "label": label,
        "model": model,
        "confidence": 0.4,
        "fallback": True,
        "reason": "heuristic_fallback",
        "backend": "heuristic",
    }


def _jev_choice(text: str, cfg: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    _ensure_typesafe_path()
    from typesafe_sdk import Choice, TypeSafeClient

    labels = candidate_labels(cfg)
    # criteria: label → 说明（含目标模型名，便于对照）
    criteria = {
        "cheap": f"简单问答／短指令；走便宜模型 {labels['cheap']}",
        "primary": f"一般任务；主模型 {labels['primary']}",
        "complex": f"多步推理／架构／深度分析；走 {labels['complex']}",
        "tool_heavy": f"大量工具／终端／改文件；走 {labels['tool_heavy']}",
        "long_context": f"长上下文／超长粘贴；走 {labels['long_context']}",
    }
    state = (
        f"user_message_summary: {(text or '')[:2000]}\n"
        f"tool_need_hint: {meta.get('tool_need') or 'unknown'}\n"
        f"session_approx_tokens: {meta.get('approx_tokens') or 0}\n"
        f"history_turns: {meta.get('history_turns') or 0}\n"
        f"budget_hint: {meta.get('budget') or 'default'}\n"
        f"bucket_hint: {meta.get('bucket') or 'none'}\n"
        "Pick the single best routing label for THIS turn."
    )
    questions = {
        "route": Choice(
            instructions=(
                "You are a model router for Hermes Agent. "
                "Choose exactly one label. Prefer cheap for trivial asks; "
                "complex for deep reasoning; tool_heavy when many tools are needed; "
                "long_context when the session/message is very long; primary otherwise."
            ),
            criteria=criteria,
        )
    }
    model = str(cfg.get("jev_model") or "jev-latest")
    with TypeSafeClient(model=model) as client:
        result = client.system_one(state=state, questions=questions, model=model)
    ans = result.choices.get("route")
    label = (ans.choice if ans else None) or "primary"
    if label not in labels:
        label = "primary"
    confidence = float(ans.confidence) if ans and ans.confidence is not None else 0.5
    probs = (
        {k: float(v) for k, v in dict(ans.probabilities).items()}
        if ans and getattr(ans, "probabilities", None) is not None
        else None
    )
    return {
        "label": label,
        "model": labels[label],
        "confidence": confidence,
        "probabilities": probs,
        "fallback": False,
        "reason": "jev_choice",
        "backend": "typesafe",
        "jev_model": getattr(result, "model", None) or model,
    }


def route_turn(
    user_message: str,
    *,
    cfg: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """返回选型；低置信／超时／异常 → primary_model，不阻塞。"""
    cfg = cfg or load_settings()
    meta = dict(meta or {})
    primary = str(cfg["primary_model"])
    min_conf = float(cfg.get("min_confidence") or 0.55)
    timeout = float(cfg.get("timeout_seconds") or 8.0)

    if not cfg.get("routing_enabled", True):
        out = {
            "label": "primary",
            "model": primary,
            "confidence": 1.0,
            "fallback": False,
            "reason": "routing_disabled",
            "backend": "off",
        }
        return out

    if dry_run or not os.environ.get("TYPESAFE_API_KEY"):
        out = _heuristic_route(user_message, cfg, meta)
        out["reason"] = "dry_run_or_missing_key" if dry_run or not os.environ.get("TYPESAFE_API_KEY") else out["reason"]
        if out["confidence"] < min_conf:
            out.update(model=primary, label="primary", fallback=True, reason="low_confidence_fallback")
        return out

    def _call() -> dict[str, Any]:
        return _jev_choice(user_message, cfg, meta)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_call)
            out = fut.result(timeout=timeout)
    except FuturesTimeout:
        out = {
            "label": "primary",
            "model": primary,
            "confidence": 0.0,
            "fallback": True,
            "reason": "jev_timeout",
            "backend": "typesafe",
        }
    except Exception as exc:
        out = {
            "label": "primary",
            "model": primary,
            "confidence": 0.0,
            "fallback": True,
            "reason": f"jev_error:{type(exc).__name__}",
            "backend": "typesafe",
            "error": str(exc)[:200],
        }

    if float(out.get("confidence") or 0) < min_conf:
        out = {
            **out,
            "model": primary,
            "label": "primary",
            "fallback": True,
            "reason": "low_confidence_fallback",
            "original_label": out.get("label"),
            "original_model": out.get("model"),
        }
    return out


def cache_decision(key: str, decision: dict[str, Any]) -> None:
    with _lock:
        _TURN_DECISIONS[key] = decision


def pop_decision(key: str) -> dict[str, Any] | None:
    with _lock:
        return _TURN_DECISIONS.get(key)


def decide_and_cache(
    *,
    session_id: str = "",
    turn_id: str = "",
    user_message: str = "",
    conversation_history: list | None = None,
    model: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_settings()
    hist = conversation_history or []
    approx = sum(len(str((m or {}).get("content") or "")) for m in hist if isinstance(m, dict))
    meta = {
        "approx_tokens": approx // 4,
        "history_turns": len(hist),
        "tool_need": "unknown",
        "budget": "default",
        "current_model": model,
    }
    decision = route_turn(user_message or "", cfg=cfg, meta=meta)
    key = turn_id or session_id or "default"
    cache_decision(key, decision)
    if session_id and session_id != key:
        cache_decision(session_id, decision)
    emit(
        str(cfg["event_log"]),
        "route_decision",
        session_id=session_id,
        turn_id=turn_id,
        decision=decision,
    )
    return decision
