"""TrimThenCompress 上下文引擎：compress() 内强制先 trim 再 compress。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

try:
    from .events import emit
    from .settings import load_settings
    from .trim import estimate_chars, trim_messages
except ImportError:
    from events import emit
    from settings import load_settings
    from trim import estimate_chars, trim_messages

try:
    from agent.context_engine import ContextEngine
except Exception:  # 独立评测时无 Hermes 路径
    class ContextEngine:  # type: ignore
        last_prompt_tokens = 0
        last_completion_tokens = 0
        last_total_tokens = 0
        threshold_tokens = 0
        context_length = 0
        compression_count = 0
        threshold_percent = 0.75
        protect_first_n = 3
        protect_last_n = 6

        def update_from_response(self, usage): ...
        def should_compress(self, prompt_tokens=None): ...
        def compress(self, messages, current_tokens=None, focus_topic=None, force=False, memory_context=""): ...


def _deterministic_compress(messages: list[dict[str, Any]], *, focus_topic: str | None = None) -> list[dict[str, Any]]:
    """不依赖 auxiliary LLM 的可复现压缩：把中间非系统消息收成一条摘要。
    真实 Hermes 会话中仍走本引擎；auxiliary 压缩前也会被 pre_auxiliary_call 记录。
    """
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) <= 4:
        return messages
    head = rest[:1]
    tail = rest[-3:]
    mid = rest[1:-3]
    mid_chars = estimate_chars(mid)
    topic = f" focus={focus_topic}" if focus_topic else ""
    summary = {
        "role": "user",
        "content": (
            f"[jev-router compressed summary{topic}] "
            f"Dropped {len(mid)} mid messages (~{mid_chars} chars). "
            "Earlier tool noise removed; keep constraints from system + recent turns."
        ),
    }
    return system + head + [summary] + tail


class TrimThenCompressEngine(ContextEngine):
    """name 必须与 config.yaml context.engine 一致：trim_compress。"""

    def __init__(self, context_length: int = 128000, plugin_settings: dict | None = None):
        self._plugin_settings = plugin_settings or {}
        self.context_length = context_length
        self.threshold_percent = 0.75
        self.threshold_tokens = int(context_length * self.threshold_percent)
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
        self.protect_first_n = 3
        self.protect_last_n = 6
        self._last_pipeline: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "trim_compress"

    def clone_for_agent(self):
        return TrimThenCompressEngine(
            context_length=self.context_length,
            plugin_settings=dict(self._plugin_settings),
        )

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        usage = usage or {}
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.last_total_tokens = int(
            usage.get("total_tokens")
            or (self.last_prompt_tokens + self.last_completion_tokens)
        )

    def update_model(self, model, context_length, **kwargs):  # noqa: ANN001
        if context_length:
            self.context_length = int(context_length)
            self.threshold_tokens = int(self.context_length * self.threshold_percent)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        tokens = int(prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens or 0)
        thr = self.threshold_tokens or int(self.context_length * self.threshold_percent)
        return tokens >= thr if thr else False

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
        force: bool = False,
        memory_context: str = "",
    ) -> List[Dict[str, Any]]:
        cfg = load_settings(self._plugin_settings)
        event_log = str(cfg["event_log"])
        msgs = copy.deepcopy(messages)
        pipeline: list[dict[str, Any]] = []

        # --- 步骤 1：TRIM（可关；关则跳过但仍记事件）---
        if cfg.get("trim_enabled", True):
            trimmed, stats = trim_messages(
                msgs,
                keep_last_n_turns=int(cfg.get("keep_last_n_turns") or 6),
                drop_old_tool_noise=bool(cfg.get("drop_old_tool_noise", True)),
            )
            pipeline.append({"step": "trim", "order": 1, **stats})
            emit(event_log, "trim", order=1, **stats)
            msgs = trimmed
        else:
            stats = {
                "chars_before": estimate_chars(msgs),
                "chars_after": estimate_chars(msgs),
                "skipped": True,
            }
            pipeline.append({"step": "trim", "order": 1, "skipped": True, **stats})
            emit(event_log, "trim_skipped", order=1, **stats)

        # --- 步骤 2：COMPRESS（永远在 trim 之后）---
        before_c = estimate_chars(msgs)
        compressed = _deterministic_compress(msgs, focus_topic=focus_topic)
        after_c = estimate_chars(compressed)
        cstats = {
            "chars_before": before_c,
            "chars_after": after_c,
            "messages_before": len(msgs),
            "messages_after": len(compressed),
            "focus_topic": focus_topic,
            "current_tokens": current_tokens,
            "force": force,
        }
        pipeline.append({"step": "compress", "order": 2, **cstats})
        emit(event_log, "compress", order=2, **cstats)

        self.compression_count += 1
        self._last_pipeline = pipeline
        emit(
            event_log,
            "trim_then_compress_pipeline",
            compression_count=self.compression_count,
            pipeline=pipeline,
            order_ok=pipeline[0]["step"].startswith("trim") and pipeline[1]["step"] == "compress",
        )
        return compressed

    def get_status(self) -> dict:
        return {
            "engine": self.name,
            "compression_count": self.compression_count,
            "last_pipeline": self._last_pipeline,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
        }
