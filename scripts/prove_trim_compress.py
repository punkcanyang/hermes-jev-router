#!/usr/bin/env python3
"""构造超长会话，证明 compress 管线顺序为 trim → compress。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import TrimThenCompressEngine  # noqa: E402
from settings import load_settings  # noqa: E402

OUT_MD = ROOT / "notes" / "reports" / "trim-compress-proof.md"
OUT_JSON = ROOT / "notes" / "reports" / "trim-compress-proof.json"


def build_long_session(n_turns: int = 40) -> list[dict]:
    msgs = [{"role": "system", "content": "你是 Hermes。系统约束：勿泄露密钥。"}]
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"用户轮 {i}: 请继续处理任务 {i}."})
        msgs.append({
            "role": "assistant",
            "content": f"助手轮 {i}: 调用工具中…",
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "terminal", "arguments": "{}"}}],
        })
        msgs.append({
            "role": "tool",
            "tool_call_id": f"c{i}",
            "content": ("工具噪声输出 " + ("x" * 200) + f" turn={i}\n") * 5,
        })
    return msgs


def main() -> int:
    cfg = load_settings()
    proof_log = ROOT / "notes" / "reports" / "trim-compress-events.jsonl"
    cfg = {**cfg, "event_log": str(proof_log)}
    proof_log.unlink(missing_ok=True)

    engine = TrimThenCompressEngine(context_length=32000, plugin_settings=cfg)
    engine.threshold_tokens = 1
    msgs = build_long_session(40)
    assert engine.should_compress(prompt_tokens=99999)
    out = engine.compress(msgs, current_tokens=99999, focus_topic="routing-proof")
    pipeline = (engine.get_status().get("last_pipeline") or [])

    events = []
    if proof_log.is_file():
        events = [json.loads(l) for l in proof_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    types = [e.get("type") for e in events]
    order_ok = False
    if "trim" in types and "compress" in types:
        order_ok = types.index("trim") < types.index("compress")
    elif len(pipeline) >= 2:
        order_ok = str(pipeline[0].get("step", "")).startswith("trim") and pipeline[1].get("step") == "compress"

    cfg_off = {**cfg, "trim_enabled": False, "event_log": str(ROOT / "notes" / "reports" / "trim-off-events.jsonl")}
    Path(cfg_off["event_log"]).unlink(missing_ok=True)
    engine2 = TrimThenCompressEngine(context_length=32000, plugin_settings=cfg_off)
    engine2.threshold_tokens = 1
    engine2.compress(msgs, current_tokens=99999)
    pipe_off = engine2.get_status().get("last_pipeline") or []

    report = {
        "order_ok": order_ok,
        "pipeline": pipeline,
        "event_types": types,
        "messages_in": len(msgs),
        "messages_out": len(out),
        "trim_off_pipeline": pipe_off,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# trim → compress 证明", "",
        f"- order_ok：**{order_ok}**",
        f"- 输入消息数：{len(msgs)} → 输出：{len(out)}",
        f"- 事件类型顺序：{types}", "",
        "## 管线", "", "```json", json.dumps(pipeline, ensure_ascii=False, indent=2), "```", "",
        "## 关裁剪（trim_enabled=false）", "",
        "```json", json.dumps(pipe_off, ensure_ascii=False, indent=2), "```", "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"order_ok": order_ok, "messages_in": len(msgs), "messages_out": len(out)}, ensure_ascii=False))
    print("wrote", OUT_MD)
    return 0 if order_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
