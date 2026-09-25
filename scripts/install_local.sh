#!/usr/bin/env bash
# 把本仓 symlink 进 ~/.hermes/plugins/jev-router 并 enable
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${HERMES_HOME:-$HOME/.hermes}/plugins/jev-router"
mkdir -p "$(dirname "$DEST")"
if [[ -e "$DEST" || -L "$DEST" ]]; then
  rm -rf "$DEST"
fi
ln -s "$SRC" "$DEST"
echo "symlink: $DEST -> $SRC"

CFG="${HERMES_HOME:-$HOME/.hermes}/jev-router.yaml"
if [[ ! -f "$CFG" ]]; then
  cp "$SRC/config.example.yaml" "$CFG"
  echo "wrote $CFG"
fi

export PATH="$HOME/.local/bin:$PATH"
hermes plugins enable jev-router 2>/dev/null || true

python3 - <<'PY'
from pathlib import Path
import os
try:
    import yaml
except ImportError:
    raise SystemExit(0)
home = Path(os.environ.get("HERMES_HOME") or Path.home()/".hermes")
cfg_path = home/"config.yaml"
data = {}
if cfg_path.is_file():
    data = yaml.safe_load(cfg_path.read_text()) or {}
ctx = data.setdefault("context", {})
if ctx.get("engine") in (None, "", "compressor"):
    ctx["engine"] = "trim_compress"
    print(f"set context.engine=trim_compress in {cfg_path}")
else:
    print(f"leave context.engine={ctx.get('engine')!r} (set manually to trim_compress to use trim→compress)")
plugins = data.setdefault("plugins", {})
entries = plugins.setdefault("entries", {})
ent = entries.setdefault("jev-router", {})
ent["enabled"] = True
cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
print("enabled plugins.entries.jev-router")
PY

echo "---- hermes plugins list ----"
hermes plugins list --plain 2>/dev/null | rg -i 'jev|trim' || true
echo "---- doctor ----"
hermes plugins doctor "$DEST" 2>&1 | tail -50
