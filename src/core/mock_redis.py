"""Standalone MockRedis for use when Redis server is unavailable.

This is a copy of the MockRedis from tests/test_frontier.py,
placed here so the production code doesn't need to import from tests/.
"""

from __future__ import annotations

from typing import Any


class MockRedis:
    """Mock Redis client for when Redis server is offline."""

    def __init__(self) -> None:
        self.sets: dict[str, set[bytes]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def ping(self) -> bool:
        return True

    def delete(self, key: str) -> None:
        self.sets.pop(key, None)
        self.zsets.pop(key, None)

    def sadd(self, key: str, member: str | bytes) -> int:
        member_bytes = member.encode("utf-8") if isinstance(member, str) else member

        if key not in self.sets:
            self.sets[key] = set()

        if member_bytes in self.sets[key]:
            return 0
        self.sets[key].add(member_bytes)
        return 1

    def srem(self, key: str, member: str | bytes) -> int:
        member_bytes = member.encode("utf-8") if isinstance(member, str) else member

        if key in self.sets and member_bytes in self.sets[key]:
            self.sets[key].remove(member_bytes)
            return 1
        return 0

    def scard(self, key: str) -> int:
        return len(self.sets.get(key, set()))

    def smembers(self, key: str) -> set[bytes]:
        return set(self.sets.get(key, set()))

    def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self.zsets:
            self.zsets[key] = {}

        added = 0
        for member, score in mapping.items():
            if member not in self.zsets[key]:
                added += 1
            self.zsets[key][member] = score
        return added

    def zrem(self, key: str, member: str) -> int:
        if key in self.zsets and member in self.zsets[key]:
            del self.zsets[key][member]
            return 1
        return 0

    def zrangebyscore(
        self, key: str, min_val: str | float, max_val: str | float, start: int | None = None, num: int | None = None
    ) -> list[bytes]:
        if key not in self.zsets:
            return []

        sorted_items = sorted(self.zsets[key].items(), key=lambda x: (x[1], x[0]))
        results = [m.encode("utf-8") for m, s in sorted_items]

        if start is not None and num is not None:
            results = results[start : start + num]
        return results

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> bytes | None:
        """Mock Lua evaluation for popping min element."""
        queue_key = keys_and_args[0]
        processing_key = keys_and_args[1]

        if queue_key not in self.zsets or not self.zsets[queue_key]:
            return None

        sorted_items = sorted(self.zsets[queue_key].items(), key=lambda x: (x[1], x[0]))
        min_member, _min_score = sorted_items[0]

        del self.zsets[queue_key][min_member]
        self.sadd(processing_key, min_member)

        return min_member.encode("utf-8")
