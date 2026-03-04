#!/usr/bin/env python3
"""Global Sentinel V4 — Agent Queue Manager

Manages task queues for OpenClaw-Ops and OpenClaw-Research bots.
Dynamic scaling based on queue depth, latency, and failure rates.
"""

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TaskQueue:
    """Priority task queue with metrics."""

    def __init__(self, name: str, max_size: int = 100):
        self.name = name
        self.max_size = max_size
        self._queue = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self.total_enqueued = 0
        self.total_completed = 0
        self.total_failed = 0
        self.total_latency_ms = 0

    def enqueue(self, task: dict) -> bool:
        with self._lock:
            if len(self._queue) >= self.max_size:
                return False
            task["enqueued_at"] = datetime.now(timezone.utc).isoformat()
            task["status"] = "queued"
            self._queue.append(task)
            self.total_enqueued += 1
            return True

    def dequeue(self) -> dict | None:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    def complete(self, latency_ms: float):
        self.total_completed += 1
        self.total_latency_ms += latency_ms

    def fail(self):
        self.total_failed += 1

    @property
    def depth(self) -> int:
        return len(self._queue)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_completed == 0:
            return 0
        return self.total_latency_ms / self.total_completed

    @property
    def failure_rate(self) -> float:
        total = self.total_completed + self.total_failed
        if total == 0:
            return 0
        return self.total_failed / total

    def metrics(self) -> dict:
        return {
            "queue": self.name,
            "depth": self.depth,
            "total_enqueued": self.total_enqueued,
            "total_completed": self.total_completed,
            "total_failed": self.total_failed,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "failure_rate": round(self.failure_rate, 4),
        }


class AgentQueueManager:
    """Manages queues and scaling decisions for both bots."""

    def __init__(self):
        self.ops_queue = TaskQueue("openclaw-ops")
        self.research_queue = TaskQueue("openclaw-research")

    def should_scale_up(self, queue: TaskQueue) -> bool:
        """Decide if more agents should be spawned."""
        if queue.depth > 5:
            return True
        if queue.avg_latency_ms > 30000:  # 30 seconds
            return True
        if queue.failure_rate > 0.2:
            return True
        return False

    def should_scale_down(self, queue: TaskQueue, active_agents: int) -> bool:
        """Decide if agents can be released."""
        if queue.depth == 0 and active_agents > 1:
            return True
        return False

    def scaling_recommendation(self, active_ops: int = 0, active_research: int = 0) -> dict:
        return {
            "ops": {
                "scale_up": self.should_scale_up(self.ops_queue),
                "scale_down": self.should_scale_down(self.ops_queue, active_ops),
                "metrics": self.ops_queue.metrics(),
            },
            "research": {
                "scale_up": self.should_scale_up(self.research_queue),
                "scale_down": self.should_scale_down(self.research_queue, active_research),
                "metrics": self.research_queue.metrics(),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def main():
    mgr = AgentQueueManager()
    rec = mgr.scaling_recommendation()
    print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
