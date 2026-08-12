"""Redis-backed priority frontier for crawl state management.

Implements the crawl frontier as a Redis sorted set:
- push(node, priority_score) — add a URL/state to the frontier
- pop_min() — pop the highest-priority item (lowest score = highest priority)
- Budget-aware size() check
- Deduplication via a visited set
- Concurrent-safe: two workers popping from the same frontier never process the same state

Spec reference: master-build-prompt §3, Step D
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    import redis


def _normalize_url_for_fp(url: str) -> str:
    """Normalize a URL for fingerprint dedup.

    Decodes percent-encoded characters in the path (e.g. ``%7C`` → ``|``)
    and lowercases scheme/host so the same page discovered via different
    encodings is not enqueued twice. Mirrors the normalization in
    ``mining.normalize_url`` and ``DiscoveredEndpoint.fingerprint``.
    """
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path)
    if decoded_path.endswith("/") and len(decoded_path) > 1:
        decoded_path = decoded_path.rstrip("/")
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=decoded_path,
        fragment="",
    ).geturl()


@dataclass
class FrontierNode:
    """A node in the crawl frontier."""

    url: str
    method: str = "GET"
    depth: int = 0
    priority_score: float = 0.0
    state_hash: str | None = None
    parent_url: str | None = None
    discovered_via: str = "seed"
    endpoint_type: str = "unknown"
    requires_auth: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON for Redis storage."""
        return json.dumps(
            {
                "url": self.url,
                "method": self.method,
                "depth": self.depth,
                "priority_score": self.priority_score,
                "state_hash": self.state_hash,
                "parent_url": self.parent_url,
                "discovered_via": self.discovered_via,
                "endpoint_type": self.endpoint_type,
                "requires_auth": self.requires_auth,
                "metadata": self.metadata,
            }
        )

    @classmethod
    def from_json(cls, data: str) -> FrontierNode:
        """Deserialize from JSON."""
        d = json.loads(data)
        return cls(
            url=d["url"],
            method=d.get("method", "GET"),
            depth=d.get("depth", 0),
            priority_score=d.get("priority_score", 0.0),
            state_hash=d.get("state_hash"),
            parent_url=d.get("parent_url"),
            discovered_via=d.get("discovered_via", "seed"),
            endpoint_type=d.get("endpoint_type", "unknown"),
            requires_auth=d.get("requires_auth", False),
            metadata=d.get("metadata", {}),
        )

    @property
    def fingerprint(self) -> str:
        """Unique key for dedup. Uses state_hash if available, else URL+method.

        The URL is normalized (percent-encoded chars decoded, scheme/host
        lowercased, trailing slash stripped) so that ``/model/a%7Cb`` and
        ``/model/a|b`` produce the same fingerprint and the same page is
        not enqueued twice.
        """
        if self.state_hash:
            return f"{self.method}::{self.state_hash}"
        return f"{self.method}::{_normalize_url_for_fp(self.url)}"


class RedisFrontier:
    """Redis-backed priority queue for the crawl frontier.

    Uses a sorted set where score = priority (lower = higher priority).
    Uses a separate set for deduplication (visited/queued tracking).
    Uses WATCH/MULTI for concurrent safety.

    Usage:
        frontier = RedisFrontier(redis_client, job_id="abc123")
        frontier.push(FrontierNode(url="https://example.com", priority_score=0.0))
        node = frontier.pop_min()
    """

    def __init__(
        self,
        redis_client: redis.Redis,  # type: ignore[type-arg]
        job_id: str,
        max_size: int = 10000,
        owner_id: str | None = None,
    ) -> None:
        self._redis = redis_client
        self._job_id = job_id
        self._max_size = max_size
        self._owner_id = owner_id

        # Tenant-scoped Redis keys so two owners never share frontier state
        try:
            from src.core.tenant_isolation import frontier_key_prefix

            prefix = frontier_key_prefix(owner_id, job_id)
        except Exception:
            prefix = f"frontier:{job_id}"
        self._queue_key = f"{prefix}:queue"  # Sorted set
        self._seen_key = f"{prefix}:seen"  # Set of fingerprints
        self._processing_key = f"{prefix}:processing"  # Set of currently-processing items

    @property
    def job_id(self) -> str:
        return self._job_id

    @property
    def max_size(self) -> int:
        return self._max_size

    def push(self, node: FrontierNode) -> bool:
        """Add a node to the frontier.

        Returns True if the node was added (not a duplicate and within budget).
        Returns False if the node was already seen or the frontier is full.
        """
        fp = node.fingerprint

        # Check if already seen (atomic check-and-add)
        if not self._redis.sadd(self._seen_key, fp):
            return False  # Already seen

        # Check budget
        current_size = self._redis.zcard(self._queue_key)
        if current_size >= self._max_size:
            # Remove from seen set since we're not actually adding it
            self._redis.srem(self._seen_key, fp)
            return False  # Frontier full

        # Add to sorted set with priority score
        self._redis.zadd(self._queue_key, {node.to_json(): node.priority_score})
        return True

    def push_many(self, nodes: list[FrontierNode]) -> int:
        """Add multiple nodes at once. Returns count of successfully added nodes."""
        added = 0
        for node in nodes:
            if self.push(node):
                added += 1
        return added

    def pop_min(self) -> FrontierNode | None:
        """Pop the highest-priority item (lowest score).

        Uses a Lua script for atomicity — ensures two concurrent workers
        never pop the same item.
        """
        # Lua script: atomically pop the minimum element from the sorted set
        lua_script = """
        local result = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', '+inf', 'LIMIT', 0, 1)
        if #result == 0 then
            return nil
        end
        redis.call('ZREM', KEYS[1], result[1])
        redis.call('SADD', KEYS[2], result[1])
        return result[1]
        """
        result = self._redis.eval(lua_script, 2, self._queue_key, self._processing_key)

        if result is None:
            return None

        data = result.decode("utf-8") if isinstance(result, bytes) else str(result)
        return FrontierNode.from_json(data)

    def mark_done(self, node: FrontierNode) -> None:
        """Mark a node as done processing (remove from processing set)."""
        self._redis.srem(self._processing_key, node.to_json())

    def requeue_processing(self) -> int:
        """Requeue all items stuck in processing (for crash recovery).

        Returns the number of items requeued.
        """
        processing = self._redis.smembers(self._processing_key)
        count = 0
        for item_bytes in processing:
            item = item_bytes.decode("utf-8") if isinstance(item_bytes, bytes) else str(item_bytes)
            try:
                node = FrontierNode.from_json(item)
                self._redis.zadd(self._queue_key, {item: node.priority_score})
                self._redis.srem(self._processing_key, item_bytes)
                count += 1
            except (json.JSONDecodeError, KeyError):
                # Corrupted item, just remove it
                self._redis.srem(self._processing_key, item_bytes)
        return count

    def size(self) -> int:
        """Return the number of items waiting in the frontier."""
        return self._redis.zcard(self._queue_key)

    def seen_count(self) -> int:
        """Return total number of unique URLs/states ever seen."""
        return self._redis.scard(self._seen_key)

    def processing_count(self) -> int:
        """Return the number of items currently being processed."""
        return self._redis.scard(self._processing_key)

    def is_empty(self) -> bool:
        """Return True if the frontier has no waiting items."""
        return self.size() == 0

    def is_within_budget(self) -> bool:
        """Return True if the frontier hasn't exceeded its max size."""
        return self.size() < self._max_size

    def peek(self, count: int = 5) -> list[FrontierNode]:
        """Peek at the top N items without removing them."""
        results = self._redis.zrangebyscore(self._queue_key, "-inf", "+inf", start=0, num=count)
        nodes = []
        for item in results:
            data = item.decode("utf-8") if isinstance(item, bytes) else str(item)
            try:
                nodes.append(FrontierNode.from_json(data))
            except (json.JSONDecodeError, KeyError):
                continue
        return nodes

    def clear(self) -> None:
        """Clear the entire frontier (queue + seen + processing)."""
        self._redis.delete(self._queue_key)
        self._redis.delete(self._seen_key)
        self._redis.delete(self._processing_key)

    def stats(self) -> dict[str, int]:
        """Return frontier statistics."""
        return {
            "queued": self.size(),
            "seen": self.seen_count(),
            "processing": self.processing_count(),
            "max_size": self._max_size,
        }
