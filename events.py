"""事件落盘：路由判决与 trim→compress 顺序证明。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def emit(event_log: str, event_type: str, **payload: Any) -> None:
    path = Path(event_log).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "type": event_type,
        **payload,
    }
    line = json.dumps(row, ensure_ascii=False, default=str)
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
