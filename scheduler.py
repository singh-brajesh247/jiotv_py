"""Small recurring task scheduler using Python threads."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .utils import log


class Scheduler:
    def __init__(self) -> None:
        self._tasks: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()

    def add(self, task_id: str, interval_seconds: float, task: Callable[[], None]) -> None:
        with self._lock:
            self.delete(task_id)

            def run_and_reschedule() -> None:
                try:
                    task()
                except Exception as exc:  # noqa: BLE001
                    log.error("Task failed: %s", exc)
                self.add(task_id, interval_seconds, task)

            timer = threading.Timer(interval_seconds, run_and_reschedule)
            timer.daemon = True
            self._tasks[task_id] = timer
            timer.start()
            log.info("Task added with ID: %s", task_id)

    def delete(self, task_id: str) -> None:
        timer = self._tasks.pop(task_id, None)
        if timer is not None:
            timer.cancel()

    def stop(self) -> None:
        with self._lock:
            for timer in self._tasks.values():
                timer.cancel()
            self._tasks.clear()


scheduler = Scheduler()
