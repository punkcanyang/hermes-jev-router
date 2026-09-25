"""上下文裁剪：丢旧工具噪声、保留系统约束与最近 N 轮。"""
from __future__ import annotations

from typing import Any


def _role(m: dict[str, Any]) -> str:
    return str(m.get("role") or "")


def _is_toolish(m: dict[str, Any]) -> bool:
    role = _role(m)
    if role in ("tool", "function"):
        return True
    if role == "assistant" and m.get("tool_calls"):
        return True
    return False


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    n = 0
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += sum(len(str(p)) for p in c)
        if m.get("tool_calls"):
            n += len(str(m.get("tool_calls")))
    return n


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    keep_last_n_turns: int = 6,
    drop_old_tool_noise: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """返回 (trimmed, stats)。禁止在 compress 前跳过本步（由引擎强制调用）。"""
    msgs = [m for m in messages if isinstance(m, dict)]
    before = estimate_chars(msgs)
    system = [m for m in msgs if _role(m) == "system"]
    rest = [m for m in msgs if _role(m) != "system"]

    # 按「轮」：user 起新轮；保留最近 keep_last_n_turns 轮
    turns: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    for m in rest:
        if _role(m) == "user" and cur:
            turns.append(cur)
            cur = [m]
        else:
            cur.append(m)
    if cur:
        turns.append(cur)

    kept_turns = turns[-max(1, int(keep_last_n_turns)) :] if turns else []
    dropped_turns = turns[: -max(1, int(keep_last_n_turns))] if len(turns) > keep_last_n_turns else []

    kept: list[dict[str, Any]] = []
    dropped_tool = 0
    # 旧轮：可选丢弃工具噪声，仅留极短标记
    for turn in dropped_turns:
        for m in turn:
            if drop_old_tool_noise and _is_toolish(m):
                dropped_tool += 1
                continue
            if drop_old_tool_noise and _role(m) == "assistant" and len(str(m.get("content") or "")) > 400:
                kept.append({**m, "content": str(m.get("content") or "")[:200] + "\n…[trimmed]"})
            else:
                kept.append(m)

    for turn in kept_turns:
        kept.extend(turn)

    out = system + kept
    after = estimate_chars(out)
    stats = {
        "chars_before": before,
        "chars_after": after,
        "messages_before": len(msgs),
        "messages_after": len(out),
        "turns_before": len(turns),
        "turns_kept": len(kept_turns),
        "tool_msgs_dropped": dropped_tool,
        "keep_last_n_turns": keep_last_n_turns,
        "drop_old_tool_noise": drop_old_tool_noise,
    }
    return out, stats
