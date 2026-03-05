"""Background task runner with SSE event broadcasting."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

logger = logging.getLogger("job_hunter.web.task_manager")


@dataclass
class TaskEvent:
    """A single event emitted by a running task."""
    type: str  # "progress", "complete", "error"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class _TaskLogHandler(logging.Handler):
    """Logging handler that pushes log records into a TaskManager's event queue."""

    def __init__(self, queue: asyncio.Queue[TaskEvent]) -> None:
        super().__init__(level=logging.INFO)
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(TaskEvent(
                type="progress",
                message=self.format(record),
            ))
        except asyncio.QueueFull:
            pass


class TaskManager:
    """Manages a single background task with SSE event broadcasting.

    Only one task can run at a time. Subscribers receive live events
    via async generators.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None
        self._task_name: str = ""
        self._events: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=500)
        self._subscribers: list[asyncio.Queue[TaskEvent]] = []
        self._log_handler = _TaskLogHandler(self._events)
        self._result: dict[str, Any] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.is_running,
            "task_name": self._task_name if self.is_running else "",
            "last_result": self._result,
        }

    def start_task(self, name: str, coro: Any) -> bool:
        """Start a background task. Returns False if one is already running."""
        if self.is_running:
            return False

        self._task_name = name
        self._result = None
        self._events = asyncio.Queue(maxsize=500)
        self._log_handler._queue = self._events

        # Install log handler on root job_hunter logger
        root_logger = logging.getLogger("job_hunter")
        root_logger.addHandler(self._log_handler)

        async def _wrapper() -> None:
            try:
                self._broadcast(TaskEvent(type="progress", message=f"Starting {name}…"))
                result = await coro
                self._result = result if isinstance(result, dict) else {"result": "ok"}
                self._broadcast(TaskEvent(type="complete", message=f"{name} completed", data=self._result))
            except Exception as exc:
                self._result = {"error": str(exc)}
                self._broadcast(TaskEvent(type="task_error", message=f"{name} failed: {exc}"))
                logger.error("Task %s failed: %s", name, exc)
            finally:
                root_logger.removeHandler(self._log_handler)

        self._task = asyncio.create_task(_wrapper())
        return True

    def cancel_task(self) -> bool:
        if self._task and not self._task.done():
            self._task.cancel()
            return True
        return False

    def _broadcast(self, event: TaskEvent) -> None:
        """Push an event to all subscribers."""
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self) -> AsyncGenerator[TaskEvent, None]:
        """Yield events as they arrive. Use in SSE endpoints."""
        q: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        try:
            while True:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield event
                if event.type in ("complete", "task_error"):
                    break
        except asyncio.TimeoutError:
            yield TaskEvent(type="ping", message="keepalive")
        except asyncio.CancelledError:
            pass
        finally:
            self._subscribers.remove(q)

