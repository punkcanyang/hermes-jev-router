#!/usr/bin/env python3
"""硬禁自动同意的一次性风险确认（写／查／撤 $HERMES_HOME/jev-router/hardline-ack.json）。

  python3 scripts/hardline_ack.py --status
  python3 scripts/hardline_ack.py            # 交互：须完整输入确认短语
  python3 scripts/hardline_ack.py --revoke

仅有 ack 不会开启任何东西：还需 auto_approve_enabled 与 auto_approve_hardline_enabled 同时为 true。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_approve as aa  # noqa: E402

CONFIRM_PHRASE = "I UNDERSTAND HARDLINE RISK"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="只查看是否已确认")
    g.add_argument("--revoke", action="store_true", help="撤销确认（删除 ack 文件）")
    args = ap.parse_args()

    path = aa._ack_path()
    if args.status:
        print(f"{path}: {'acknowledged' if aa.hardline_risk_ack_present() else 'not acknowledged'}")
        return 0
    if args.revoke:
        aa.clear_hardline_risk_ack()
        print(f"revoked: {path}")
        return 0
    if not sys.stdin.isatty():
        print("refusing: interactive terminal required", file=sys.stderr)
        return 2

    print(aa.risk_text())
    print()
    typed = input(f'Type "{CONFIRM_PHRASE}" to acknowledge: ').strip()
    if typed != CONFIRM_PHRASE:
        print("not acknowledged", file=sys.stderr)
        return 1
    written = aa.write_hardline_risk_ack(acknowledged_by=getpass.getuser())
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
