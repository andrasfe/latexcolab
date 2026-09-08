"""Timers and worker threads, behind a tiny interface.

The app model schedules debounced saves and background compiles. On the desktop
that means GLib timeouts and threads; in tests it means running everything
synchronously, so the model can be driven without a main loop.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable


def _is_expected(exc: BaseException) -> bool:
    """Failures the app turns into a message for the user (an offline LM Studio,
    a missing engine, an unreadable file) rather than a bug worth a traceback."""
    if isinstance(exc, OSError):
        return True
    return type(exc).__module__.startswith("latexcolab.core")


class Scheduler:
    """GLib-backed scheduler used by the running application."""

    def __init__(self) -> None:
        from gi.repository import GLib  # noqa: PLC0415 - optional at import time
        self._glib = GLib

    def after(self, seconds: float, fn: Callable[[], Any]):
        """Returns a GLib.Source, so cancelling an already-fired timer is a no-op."""
        source = self._glib.timeout_source_new(max(0, int(seconds * 1000)))
        source.set_callback(lambda *_: (fn(), False)[1])
        source.attach(None)
        return source

    def cancel(self, handle) -> None:
        if handle is not None and not handle.is_destroyed():
            handle.destroy()

    def on_main(self, fn: Callable[[], Any]) -> None:
        self._glib.idle_add(lambda: (fn(), False)[1])

    def run_background(self, work: Callable[[], Any],
                       done: Callable[[Any, BaseException | None], Any] | None = None) -> None:
        def target() -> None:
            try:
                result, error = work(), None
            except BaseException as exc:  # noqa: BLE001 - reported to the caller
                result, error = None, exc
                if not _is_expected(exc):
                    traceback.print_exc()
            if done is not None:
                self.on_main(lambda: done(result, error))
        threading.Thread(target=target, daemon=True).start()


class DirectScheduler:
    """Synchronous scheduler for tests: timers are queued, work runs inline."""

    def __init__(self) -> None:
        self.pending: dict[int, Callable[[], Any]] = {}
        self._next = 1

    def after(self, seconds: float, fn: Callable[[], Any]) -> int:
        handle = self._next
        self._next += 1
        self.pending[handle] = fn
        return handle

    def cancel(self, handle: int | None) -> None:
        if handle:
            self.pending.pop(handle, None)

    def on_main(self, fn: Callable[[], Any]) -> None:
        fn()

    def run_background(self, work: Callable[[], Any],
                       done: Callable[[Any, BaseException | None], Any] | None = None) -> None:
        try:
            result, error = work(), None
        except BaseException as exc:  # noqa: BLE001
            result, error = None, exc
        if done is not None:
            done(result, error)

    def flush(self) -> None:
        """Fire every queued timer (in scheduling order)."""
        while self.pending:
            handle = min(self.pending)
            fn = self.pending.pop(handle)
            fn()
