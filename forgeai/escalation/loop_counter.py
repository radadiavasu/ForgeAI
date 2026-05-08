"""Loop counter tracking repeated error signatures per task."""

from __future__ import annotations

import asyncio
import threading


class LoopCounter:
    """Track repeated failures and indicate when escalation should trigger."""

    def __init__(self) -> None:
        # In-memory store: Dict[task_id, Dict[error_signature, int]]
        # Phase 4 will move this to Redis. For now, in-memory is correct.
        self._counters: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()

    def increment(self, task_id: str, error_signature: str) -> int:
        """Increment counter for this task + error signature and return count."""
        with self._thread_lock:
            task_counts = self._counters.setdefault(task_id, {})
            task_counts[error_signature] = task_counts.get(error_signature, 0) + 1
            return task_counts[error_signature]

    def get(self, task_id: str, error_signature: str) -> int:
        """Return current count for this task and signature."""
        with self._thread_lock:
            return self._counters.get(task_id, {}).get(error_signature, 0)

    def reset(self, task_id: str) -> None:
        """Clear all counters for a task."""
        with self._thread_lock:
            self._counters.pop(task_id, None)

    def should_escalate(self, task_id: str, error_signature: str) -> bool:
        """Return True when this error was seen 3 or more times consecutively."""
        return self.get(task_id, error_signature) >= 3
