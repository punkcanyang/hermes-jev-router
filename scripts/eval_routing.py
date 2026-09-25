#!/usr/bin/env python3
"""≥20 条标注样本路由对照。"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from routing import route_turn  # noqa: E402
from settings import load_settings  # noqa: E402

SAMPLES = ROOT / "data" / "routing_samples.jsonl"
OUT_MD = ROOT / "notes" / "reports" / "routing-eval.md"
OUT_JSON = ROOT / "notes" / "reports" / "routing-eval.json"


def main() -> int:
    cfg = load_settings()
    dry = "--dry-run" in sys.argv or not os.environ.get("TYPESAFE_API_KEY")
    if "--force-live" in sys.argv:
        dry = False
        if not os.environ.get("TYPESAFE_API_KEY"):
            print("TYPESAFE_API_KEY missing", file=sys.stderr)
            return 2

    rows = [json.loads(l) for l in SAMPLES.read_text(encoding="utf-8").splitlines() if l.strip()]
    results = []
    for row in rows:
        meta = {"bucket": row.get("bucket"), "approx_tokens": len(row.get("text") or "") // 4}
        decision = route_turn(row["text"], cfg=cfg, meta=meta, dry_run=dry)
        expect = row.get("expect_label")
        match = decision.get("label") == expect
        soft = match or (decision.get("fallback") and decision.get("reason") == "low_confidence_fallback")
        results.append({
            "id": row["id"], "bucket": row.get("bucket"), "expect_label": expect,
            "got_label": decision.get("label"), "got_model": decision.get("model"),
            "confidence": decision.get("confidence"), "fallback": decision.get("fallback"),
            "reason": decision.get("reason"), "backend": decision.get("backend"),
            "match": match, "soft_ok": soft,
        })

    n = len(results)
    hard = sum(1 for r in results if r["match"])
    soft_n = sum(1 for r in results if r["soft_ok"])
    by_label = Counter(r["got_label"] for r in results)
    report = {
        "n": n,
        "hard_match_rate": hard / n if n else 0,
        "soft_ok_rate": soft_n / n if n else 0,
        "label_distribution": dict(by_label),
        "fallback_count": sum(1 for r in results if r["fallback"]),
        "dry_run": dry,
        "routing_enabled": cfg.get("routing_enabled"),
        "min_confidence": cfg.get("min_confidence"),
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 路由对照报告（Jev / TypeSafe）", "",
        f"- 样本数：{n}",
        f"- 硬一致率：{hard}/{n} = {report['hard_match_rate']:.1%}",
        f"- 软通过率：{soft_n}/{n} = {report['soft_ok_rate']:.1%}",
        f"- 选型分布：{dict(by_label)}",
        f"- 兜底次数：{report['fallback_count']}",
        f"- dry_run：{dry}", "",
        "| id | bucket | expect | got | model | conf | fallback | reason | match |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['bucket']} | {r['expect_label']} | {r['got_label']} | "
            f"`{r['got_model']}` | {float(r['confidence'] or 0):.2f} | {r['fallback']} | {r['reason']} | "
            f"{'Y' if r['match'] else 'N'} |"
        )
    lines += ["", "## 关路由", "", "`JEV_ROUTER_ROUTING_ENABLED=false` → 全部 primary。", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("n", "hard_match_rate", "soft_ok_rate", "label_distribution", "fallback_count", "dry_run")}, ensure_ascii=False))
    print("wrote", OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
