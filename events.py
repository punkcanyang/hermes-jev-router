"""事件落盘：路由判决与 trim→compress 顺序证明。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()

_QUOTED_OR_WORD = r"(\"[^\"]*\"|'[^']*'|\S+)"

# 顺序敏感：Bearer 规则必须先于 key=value，否则 "Authorization: Bearer tok" 只会吃掉 "Bearer"
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_.\-]*(?:api[_-]?key|token|passw(?:or)?d|secret|access[_-]?key"
            r"|private[_-]?key|credential|auth)[A-Za-z0-9_.\-]*)(\s*[=:]\s*)" + _QUOTED_OR_WORD
        ),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(r"(?i)(--?(?:password|passwd|token|api[_-]?key|secret)\s+)" + _QUOTED_OR_WORD),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|SECRET\d+)\b"
        ),
        "[REDACTED]",
    ),
]


def scrub(text: Any, limit: int = 400) -> str:
    """尽力脱敏（不是安全边界）：去掉常见密钥形态后截断。"""
    s = str(text or "")
    for pattern, repl in _REDACT_PATTERNS:
        s = pattern.sub(repl, s)
    return s[:limit]


def emit(event_log: str, event_type: str, **payload: Any) -> None:
    path = Path(event_log).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    row = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "type": event_type,
        **payload,
    }
    line = json.dumps(row, ensure_ascii=False, default=str)
    with _lock:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
