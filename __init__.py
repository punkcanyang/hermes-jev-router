"""Hermes plugin: jev-router — Jev 模型路由 + trim→compress 引擎。"""
from __future__ import annotations

import logging
from typing import Any

try:
    from .events import emit
    from .routing import decide_and_cache, pop_decision
    from .settings import load_settings
    from .engine import TrimThenCompressEngine
except ImportError:  # script / flat import
    from events import emit
    from routing import decide_and_cache, pop_decision
    from settings import load_settings
    from engine import TrimThenCompressEngine

logger = logging.getLogger("hermes.plugins.jev_router")


def register(ctx):  # noqa: ANN001
    """Hermes PluginManager 入口。"""
    plugin_settings = {}
    try:
        plugin_settings = dict(getattr(ctx, "settings", None) or {})
    except Exception:
        plugin_settings = {}

    try:
        ctx.register_context_engine(TrimThenCompressEngine(plugin_settings=plugin_settings))
    except Exception as exc:
        logger.warning("register_context_engine failed: %s", exc)

    def on_pre_llm_call(
        session_id: str = "",
        turn_id: str = "",
        user_message: str = "",
        conversation_history=None,
        model: str = "",
        **kwargs: Any,
    ):
        cfg = load_settings(plugin_settings)
        if not cfg.get("routing_enabled", True):
            emit(str(cfg["event_log"]), "route_skipped", reason="routing_disabled", session_id=session_id)
            return None
        decision = decide_and_cache(
            session_id=session_id or "",
            turn_id=turn_id or "",
            user_message=user_message or "",
            conversation_history=list(conversation_history or []),
            model=model or "",
            cfg=cfg,
        )
        return {
            "context": (
                f"[jev-router] route={decision.get('label')} model={decision.get('model')} "
                f"conf={float(decision.get('confidence') or 0):.2f} fallback={decision.get('fallback')} "
                f"reason={decision.get('reason')}"
            )
        }

    def on_llm_request(**kwargs: Any):
        cfg = load_settings(plugin_settings)
        request = dict(kwargs.get("request") or {})
        if not cfg.get("routing_enabled", True):
            return None
        turn_id = str(kwargs.get("turn_id") or "")
        session_id = str(kwargs.get("session_id") or "")
        decision = pop_decision(turn_id) or pop_decision(session_id)
        if not decision or not decision.get("model"):
            return None
        new_model = str(decision["model"])
        request["model"] = new_model
        emit(
            str(cfg["event_log"]),
            "middleware_model_override",
            from_model=kwargs.get("model"),
            to_model=new_model,
            decision=decision,
            session_id=session_id,
            turn_id=turn_id,
        )
        return {
            "request": request,
            "source": "jev-router",
            "reason": str(decision.get("reason") or "jev_route"),
        }

    def on_pre_auxiliary_call(aux_task: str = "", **kwargs: Any):
        cfg = load_settings(plugin_settings)
        if str(aux_task or "") != "compression":
            return None
        emit(
            str(cfg["event_log"]),
            "pre_auxiliary_compression",
            aux_task=aux_task,
            model=kwargs.get("model"),
            message_count=kwargs.get("message_count"),
            approx_input_tokens=kwargs.get("approx_input_tokens"),
        )
        return None

    def on_post_auxiliary_call(aux_task: str = "", **kwargs: Any):
        cfg = load_settings(plugin_settings)
        if str(aux_task or "") != "compression":
            return None
        emit(
            str(cfg["event_log"]),
            "post_auxiliary_compression",
            aux_task=aux_task,
            error=kwargs.get("error"),
        )
        return None

    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("pre_auxiliary_call", on_pre_auxiliary_call)
    ctx.register_hook("post_auxiliary_call", on_post_auxiliary_call)
    try:
        ctx.register_middleware("llm_request", on_llm_request)
    except Exception as exc:
        logger.warning("register_middleware llm_request failed: %s", exc)
