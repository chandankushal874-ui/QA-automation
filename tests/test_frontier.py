"""Tests for the RedisFrontier crawl queue."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.frontier import FrontierNode, RedisFrontier


class MockRedis:
    """Mock Redis client for unit testing RedisFrontier."""

    def __init__(self) -> None:
        self.sets: dict[str, set[bytes]] = {}
        self.zsets: dict[str, dict[str, float]] = {}

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

        # Sort items by score, then member
        sorted_items = sorted(self.zsets[key].items(), key=lambda x: (x[1], x[0]))
        results = [m.encode("utf-8") for m, s in sorted_items]

        if start is not None and num is not None:
            results = results[start : start + num]
        return results

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> bytes | None:
        """Mock Lua evaluation for popping min element.

        script: Atomically pop minimum element from keys[1] (queue) and add to keys[2] (processing).
        """
        queue_key = keys_and_args[0]
        processing_key = keys_and_args[1]

        if queue_key not in self.zsets or not self.zsets[queue_key]:
            return None

        # Find the minimum element
        sorted_items = sorted(self.zsets[queue_key].items(), key=lambda x: (x[1], x[0]))
        min_member, _min_score = sorted_items[0]

        # Atomically remove from queue
        del self.zsets[queue_key][min_member]

        # Add to processing set
        self.sadd(processing_key, min_member)

        return min_member.encode("utf-8")


@pytest.fixture
def mock_redis():
    return MockRedis()


def test_frontier_node_serialization():
    node = FrontierNode(
        url="https://example.com/about",
        method="GET",
        depth=2,
        priority_score=0.5,
        state_hash="hash123",
        parent_url="https://example.com",
        discovered_via="dom_link",
        endpoint_type="page",
        requires_auth=False,
        metadata={"title": "About Us"},
    )
    serialized = node.to_json()
    deserialized = FrontierNode.from_json(serialized)

    assert deserialized.url == node.url
    assert deserialized.method == node.method
    assert deserialized.depth == node.depth
    assert deserialized.priority_score == node.priority_score
    assert deserialized.state_hash == node.state_hash
    assert deserialized.parent_url == node.parent_url
    assert deserialized.discovered_via == node.discovered_via
    assert deserialized.endpoint_type == node.endpoint_type
    assert deserialized.requires_auth == node.requires_auth
    assert deserialized.metadata == node.metadata
    assert deserialized.fingerprint == "GET::hash123"


def test_frontier_node_fingerprint_fallback():
    node = FrontierNode(url="https://example.com", state_hash=None)
    assert node.fingerprint == "GET::https://example.com"


def test_frontier_push_and_pop(mock_redis):
    frontier = RedisFrontier(mock_redis, job_id="test_job", max_size=5)

    node1 = FrontierNode(url="https://example.com/1", priority_score=1.5)
    node2 = FrontierNode(url="https://example.com/2", priority_score=0.5)
    node3 = FrontierNode(url="https://example.com/3", priority_score=2.0)

    assert frontier.push(node1) is True
    assert frontier.push(node2) is True
    assert frontier.push(node3) is True

    # Duplicate push should return False
    assert frontier.push(node1) is False
    assert frontier.size() == 3
    assert frontier.seen_count() == 3

    # Pop should return elements in priority order (lowest score first)
    popped1 = frontier.pop_min()
    assert popped1 is not None
    assert popped1.url == "https://example.com/2"

    popped2 = frontier.pop_min()
    assert popped2 is not None
    assert popped2.url == "https://example.com/1"

    popped3 = frontier.pop_min()
    assert popped3 is not None
    assert popped3.url == "https://example.com/3"

    assert frontier.pop_min() is None
    assert frontier.size() == 0


def test_frontier_max_size_budget(mock_redis):
    frontier = RedisFrontier(mock_redis, job_id="test_job", max_size=2)

    node1 = FrontierNode(url="https://example.com/1")
    node2 = FrontierNode(url="https://example.com/2")
    node3 = FrontierNode(url="https://example.com/3")

    assert frontier.push(node1) is True
    assert frontier.push(node2) is True
    assert frontier.push(node3) is False  # Budget exceeded

    assert frontier.size() == 2
    assert frontier.seen_count() == 2


def test_frontier_processing_and_requeue(mock_redis):
    frontier = RedisFrontier(mock_redis, job_id="test_job")

    node = FrontierNode(url="https://example.com")
    frontier.push(node)

    popped = frontier.pop_min()
    assert popped is not None
    assert frontier.processing_count() == 1
    assert frontier.size() == 0

    # Mark done should remove it from processing
    frontier.mark_done(popped)
    assert frontier.processing_count() == 0

    # Test crash recovery (requeue processing)
    frontier.push(node)  # note: seen key is still populated, but mock_redis allows sadd/srem check.
    # Actually seen set contains fingerprint, to push again we need a different URL
    node2 = FrontierNode(url="https://example.com/another")
    frontier.push(node2)
    frontier.pop_min()
    assert frontier.processing_count() == 1

    # Requeue should move it back to queue
    requeued = frontier.requeue_processing()
    assert requeued == 1
    assert frontier.processing_count() == 0
    assert frontier.size() == 1
