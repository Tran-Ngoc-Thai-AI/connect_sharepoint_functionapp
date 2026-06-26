from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class SyncMetrics:
    counters: Counter = field(default_factory=Counter)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def snapshot(self) -> dict[str, int]:
        expected = [
            "files_scanned",
            "created_count",
            "updated_count",
            "deleted_count",
            "retry_count",
            "failed_downloads",
            "deadletter_count",
        ]
        return {name: self.counters.get(name, 0) for name in expected}
