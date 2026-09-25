"""带硬超时的调用：超时立即返回，不等待后台线程结束。"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable

__all__ = ["FuturesTimeout", "call_with_timeout"]


def call_with_timeout(fn: Callable[[], Any], timeout: float) -> Any:
    """在 daemon 线程里跑 fn；超时抛 FuturesTimeout，fn 的异常原样抛出。

    不用 ``with ThreadPoolExecutor``：其 __exit__ 会 shutdown(wait=True)，
    挂住的调用会让超时形同虚设。超时后线程继续在后台跑完并被丢弃。
    """
    box: queue.Queue = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            box.put((True, fn()))
        except BaseException as exc:  # noqa: BLE001
            box.put((False, exc))

    threading.Thread(target=_run, name="jev-router-call", daemon=True).start()
    try:
        ok, value = box.get(timeout=max(float(timeout), 0.0))
    except queue.Empty:
        raise FuturesTimeout() from None
    if ok:
        return value
    raise value
