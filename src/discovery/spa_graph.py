"""Single Page Application (SPA) state-flow graph planner.

Models SPA transitions that do not change URLs.
Vertices: DOM structural state hashes.
Edges: Action triggers (selector click, inputs).
Allows finding paths (series of actions) to reach deep/hidden SPA states.

Spec reference: master-build-prompt §3, Step K
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SPATransition:
    """An edge representing an action that triggers a state transition."""

    source_hash: str
    target_hash: str
    action_type: str  # click, fill, select, hover
    selector: str
    value: str | None = None
    url: str = ""  # Associated page URL


@dataclass
class SPAState:
    """A vertex representing a unique DOM structural state."""

    state_hash: str
    url: str
    title: str = ""
    interactive_selectors: list[str] = field(default_factory=list)
    html_snapshot: str = ""
    is_visited: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class SPAGraph:
    """Graph structure to represent, record, and plan routes across SPA states.

    Uses BFS to calculate the shortest sequence of actions to transition
    from a source state (e.g., initial page load) to a target state.
    """

    def __init__(self) -> None:
        self.states: dict[str, SPAState] = {}
        self.transitions: dict[str, list[SPATransition]] = {}  # source_hash -> list of edges

    def add_state(self, state: SPAState) -> None:
        """Add a unique SPA DOM state to the graph."""
        if state.state_hash not in self.states:
            self.states[state.state_hash] = state
            if state.state_hash not in self.transitions:
                self.transitions[state.state_hash] = []

    def add_transition(self, transition: SPATransition) -> None:
        """Add a transition edge between two SPA states."""
        if transition.source_hash not in self.transitions:
            self.transitions[transition.source_hash] = []

        # Avoid duplicate edges
        exists = any(
            t.target_hash == transition.target_hash
            and t.selector == transition.selector
            and t.action_type == transition.action_type
            for t in self.transitions[transition.source_hash]
        )
        if not exists:
            self.transitions[transition.source_hash].append(transition)

    def find_path(self, start_hash: str, target_hash: str) -> list[SPATransition] | None:
        """Find the shortest sequence of transitions/actions using BFS."""
        if start_hash not in self.states or target_hash not in self.states:
            return None
        if start_hash == target_hash:
            return []

        # BFS queue storing (current_hash, path_taken)
        queue = deque([(start_hash, [])])
        visited = {start_hash}

        while queue:
            current, path = queue.popleft()

            if current == target_hash:
                return path

            for edge in self.transitions.get(current, []):
                if edge.target_hash not in visited:
                    visited.add(edge.target_hash)
                    queue.append((edge.target_hash, [*path, edge]))

        return None  # Unreachable

    def get_unvisited_states(self) -> list[SPAState]:
        """Get list of registered states that haven't been crawled yet."""
        return [s for s in self.states.values() if not s.is_visited]

    def clear(self) -> None:
        """Clear the graph."""
        self.states.clear()
        self.transitions.clear()


# ─── DOM Hash Helpers ───


def compute_python_dom_hash(html_content: str) -> str:
    """Fallback Python-only DOM structural hash generation.

    Extracts tags, classes, and IDs from HTML structure to create a fingerprint.
    """
    # Find all button, input, form, select, and elements with onclick handlers
    tags = re.findall(r"<([a-zA-Z0-9]+)\b([^>]*)>", html_content)
    fingerprints = []

    for tag, attrs in tags:
        tag = tag.lower()
        if tag not in ("button", "input", "form", "select", "a", "div"):
            continue

        # Check if div has click/link related attributes to include it
        is_interactive_div = tag == "div" and any(x in attrs for x in ("onclick", "data-", "href", "role"))
        if tag == "div" and not is_interactive_div:
            continue

        # Extract id, class, onclick attributes
        el_id = re.search(r'id=["\'`](.*?)["\'`]', attrs)
        el_class = re.search(r'class=["\'`](.*?)["\'`]', attrs)
        el_onclick = re.search(r'onclick=["\'`](.*?)["\'`]', attrs)

        id_str = el_id.group(1) if el_id else ""
        class_str = el_class.group(1) if el_class else ""
        onclick_str = el_onclick.group(1) if el_onclick else ""

        fingerprints.append(f"{tag}:{id_str}:{class_str}:{onclick_str}")

    fingerprints.sort()
    combined = "|".join(fingerprints)
    return hashlib.md5(combined.encode("utf-8")).hexdigest()


# Playwright JS injection snippet to compute DOM hash on live pages
LIVE_DOM_HASH_JS = """
(() => {
    const elements = Array.from(document.querySelectorAll('button, input, form, select, a, [onclick], [data-url], [data-href]'));
    const structure = elements.map(el => {
        const onclick = el.getAttribute('onclick') || '';
        const dataUrl = el.getAttribute('data-url') || el.getAttribute('data-href') || '';
        return `${el.tagName.toLowerCase()}:${el.id || ''}:${el.className || ''}:${onclick}:${dataUrl}`;
    }).sort().join('|');

    // Hash generator
    let hash = 0;
    for (let i = 0; i < structure.length; i++) {
        hash = (hash << 5) - hash + structure.charCodeAt(i);
        hash |= 0;
    }
    return Math.abs(hash).toString(16);
})()
"""
