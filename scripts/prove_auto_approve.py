#!/usr/bin/env python3
"""$0 自检：自动同意 fail-closed 矩阵 + 脱敏 + 超时真能返回。不打 TypeSafe。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_HOME = tempfile.mkdtemp(prefix="jev-router-proof-")
os.environ["HERMES_HOME"] = _HOME
for _k in list(os.environ):
    if _k.startswith(("JEV_ROUTER_", "HERMES_JEV_")) or _k == "TYPESAFE_API_KEY":
        del os.environ[_k]

import auto_approve as aa  # noqa: E402
import routing  # noqa: E402
from events import scrub  # noqa: E402

SECRET_CMD = "curl -H 'Authorization: Bearer tok_abc123XYZ' https://u:hunter2@example.com && rm -rf /tmp/x"


@dataclass(frozen=True)
class FakeRequest:
    """字段与宿主 hermes_cli.approval_policy.ApprovalPolicyRequest 一致。"""

    schema_version: int = 1
    request_id: str = "r1"
    digest: str = "d" * 64
    command: str = SECRET_CMD
    description: str = "delete temp dir"
    pattern_key: str = "rm_recursive"
    pattern_keys: tuple = field(default_factory=lambda: ("rm_recursive",))
    surface: str = "cli"
    session_key: str = "s1"
    turn_id: str = "t1"
    tool_call_id: str = "c1"
    is_hardline: bool = False
    timeout_seconds: float = 8.0


RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})


def set_env(**kv: str | None) -> None:
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def decide(req: FakeRequest | object | None = None, settings: dict | None = None) -> str:
    return aa.build_policy_callback(settings)(req or FakeRequest())["decision"]


def main() -> int:
    cfg_path = Path(_HOME) / "jev-router.yaml"

    check("default_off", decide() == "needs_human")
    set_env(JEV_ROUTER_AUTO_APPROVE_MOCK="approve")
    check("mock_set_but_flag_off", decide() == "needs_human")

    cfg_path.write_text('jev_router:\n  auto_approve_enabled: "false"\n', encoding="utf-8")
    check("yaml_string_false_stays_off", decide() == "needs_human")
    cfg_path.write_text("jev_router:\n  auto_approve_enabled: true\n", encoding="utf-8")
    check("yaml_block_true_enables", decide() == "once")
    cfg_path.unlink()

    set_env(JEV_ROUTER_AUTO_APPROVE_ENABLED="true")
    check("enabled_mock_approve_once", decide() == "once")
    set_env(JEV_ROUTER_AUTO_APPROVE_ENABLED="ture")
    check("typo_env_stays_off", decide() == "needs_human")
    set_env(JEV_ROUTER_AUTO_APPROVE_ENABLED="true")

    for kind in ("deny", "unsure", "low_confidence", "malformed", "error", "garbage"):
        set_env(JEV_ROUTER_AUTO_APPROVE_MOCK=kind)
        check(f"mock_{kind}_needs_human", decide() == "needs_human")

    set_env(JEV_ROUTER_AUTO_APPROVE_MOCK="low_confidence")
    check("threshold_floor", decide(settings={"auto_approve_confidence": 0.1}) == "needs_human")

    set_env(JEV_ROUTER_AUTO_APPROVE_MOCK="timeout")
    t0 = time.monotonic()
    got = decide(settings={"auto_approve_timeout_seconds": 0.3})
    elapsed = time.monotonic() - t0
    check("mock_timeout_needs_human", got == "needs_human", got)
    check("timeout_returns_promptly", elapsed < 1.0, f"{elapsed:.2f}s")
    t0 = time.monotonic()
    got = decide(FakeRequest(timeout_seconds=0.6))
    elapsed = time.monotonic() - t0
    check("respects_host_budget", got == "needs_human" and elapsed < 1.0, f"{got} {elapsed:.2f}s")

    set_env(JEV_ROUTER_AUTO_APPROVE_MOCK=None)
    check("live_missing_key_needs_human", decide() == "needs_human")

    set_env(JEV_ROUTER_AUTO_APPROVE_MOCK="approve")
    check("unsupported_schema", decide(FakeRequest(schema_version=2)) == "needs_human")

    class Broken:
        schema_version = 1
        is_hardline = False

        @property
        def command(self):  # noqa: D401
            raise RuntimeError("boom")

    check("callback_exception_needs_human", decide(Broken()) == "needs_human")

    class NoHardlineField:
        schema_version = 1

    check("missing_is_hardline_treated_as_hardline", decide(NoHardlineField()) == "needs_human")

    hard = FakeRequest(is_hardline=True)
    check("hardline_second_switch_off", decide(hard) == "needs_human")
    set_env(JEV_ROUTER_AUTO_APPROVE_HARDLINE_ENABLED="true")
    aa.clear_hardline_risk_ack()
    check("hardline_ack_missing", decide(hard) == "needs_human")
    ack = aa._ack_path()
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.write_text(json.dumps({"acknowledged": True, "ack_hash": "forged"}), encoding="utf-8")
    check("forged_ack_rejected", not aa.hardline_risk_ack_present())
    aa.write_hardline_risk_ack(acknowledged_by="proof")
    check("real_ack_accepted", aa.hardline_risk_ack_present())
    check("ack_file_mode_0600", (ack.stat().st_mode & 0o777) == 0o600, oct(ack.stat().st_mode & 0o777))
    check("hardline_mock_refused", decide(hard) == "needs_human")
    set_env(JEV_ROUTER_AUTO_APPROVE_ENABLED="false")
    check("hardline_needs_main_switch", decide(hard) == "needs_human")
    aa.clear_hardline_risk_ack()
    set_env(JEV_ROUTER_AUTO_APPROVE_HARDLINE_ENABLED=None, JEV_ROUTER_AUTO_APPROVE_ENABLED=None)

    norm = aa._normalize_jev_result
    check("normalize_rejects_nan", norm({"decision": "approve", "confidence": float("nan")}) is None)
    check("normalize_rejects_bool", norm({"decision": "approve", "confidence": True}) is None)
    check("normalize_rejects_range", norm({"decision": "approve", "confidence": 1.5}) is None)
    check("normalize_rejects_label", norm({"decision": "yes", "confidence": 0.99}) is None)
    check("map_never_session", aa.map_jev_to_host({"decision": "approve", "confidence": 1.0, "reason_code": "x"}, min_confidence=0.8) == "once")

    samples = {
        "bearer": ("Authorization: Bearer tok_abc123XYZ", "tok_abc123XYZ"),
        "url_creds": ("https://u:hunter2@example.com/x", "hunter2"),
        "env_secret": ("AWS_SECRET_ACCESS_KEY=AbCdEf123 aws s3 ls", "AbCdEf123"),
        "typesafe_key": ("TYPESAFE_API_KEY=ts_live_zzz9 python x.py", "ts_live_zzz9"),
        "flag_password": ("mysql --password s3cr3t -u root", "s3cr3t"),
        "quoted": ('export GITHUB_TOKEN="gh value here"', "gh value here"),
        "sk": ("echo sk-abcdefghijklmnop", "sk-abcdefghijklmnop"),
        "ghp": ("git push https://x ghp_" + "a" * 36, "ghp_" + "a" * 36),
    }
    for name, (text, secret) in samples.items():
        out = scrub(text)
        check(f"scrub_{name}", secret not in out, out)

    log = Path(_HOME) / "jev-router" / "events.jsonl"
    body = log.read_text(encoding="utf-8") if log.is_file() else ""
    check("event_log_written", bool(body))
    check("event_log_no_raw_command", "hunter2" not in body and "tok_abc123XYZ" not in body and "rm -rf" not in body)
    check("event_log_mode_0600", log.is_file() and (log.stat().st_mode & 0o777) == 0o600)

    class NoHost:
        pass

    class Host:
        def __init__(self):
            self.policies = {}

        def register_approval_policy(self, name, cb):
            self.policies[name] = cb

    h = Host()
    check("register_absent_host_false", aa.try_register_approval_policy(NoHost()) is False)
    check("register_host_true", aa.try_register_approval_policy(h) is True and "jev" in h.policies)

    def _hang(*_a, **_k):
        time.sleep(3)
        return {"label": "complex", "model": "x", "confidence": 0.99}

    routing._jev_choice = _hang
    set_env(TYPESAFE_API_KEY="dummy-not-a-real-key")
    t0 = time.monotonic()
    out = routing.route_turn("hello", cfg={**routing.load_settings(), "timeout_seconds": 0.3})
    elapsed = time.monotonic() - t0
    set_env(TYPESAFE_API_KEY=None)
    check("routing_timeout_falls_back_promptly", out.get("reason") == "jev_timeout" and elapsed < 1.0, f"{out.get('reason')} {elapsed:.2f}s")

    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"total": len(RESULTS), "failed": len(failed)}, ensure_ascii=False))
    for r in failed:
        print("FAIL", r["name"], r["detail"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
