"""DOM structural similarity matching engine.

Computes semantic tag path skeletons of pages to cluster them into layouts (templates)
and throttle crawling of duplicate infinite pagination structures.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from html.parser import HTMLParser


class DOMPathExtractor(HTMLParser):
    """Streaming HTML parser that extracts unique tag structural paths."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.paths: set[str] = set()
        # Ignore formatting and non-structural tags to prevent noise
        self.ignored_tags: set[str] = {
            "span",
            "b",
            "i",
            "strong",
            "em",
            "u",
            "small",
            "code",
            "pre",
            "br",
            "hr",
            "script",
            "style",
            "noscript",
            "iframe",
            "svg",
            "path",
            "meta",
            "link",
            "head",
            "html",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.ignored_tags:
            return

        # Sanitize attributes to strip dynamic digits, ids, and uuids
        id_val = ""
        class_val = ""
        for name, value in attrs:
            if not value:
                continue
            if name == "id":
                id_clean = re.sub(r"\d+", "{id}", value)
                id_val = re.sub(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    "{uuid}",
                    id_clean,
                    flags=re.IGNORECASE,
                )
            elif name == "class":
                class_clean = " ".join([re.sub(r"\d+", "{id}", c) for c in value.split()])
                class_val = re.sub(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    "{uuid}",
                    class_clean,
                    flags=re.IGNORECASE,
                )

        # Construct path segment e.g., div#product-list.item-grid
        node = tag_lower
        if id_val:
            node += f"#{id_val}"
        if class_val:
            sorted_classes = ".".join(sorted(class_val.split()))
            if sorted_classes:
                node += f".{sorted_classes}"

        self.stack.append(node)
        path = ">".join(self.stack)
        self.paths.add(path)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self.ignored_tags:
            return

        # Safely pop the stack, accounting for possibly unclosed/malformed tags
        if self.stack:
            for i in range(len(self.stack) - 1, -1, -1):
                stack_tag = self.stack[i].split("#")[0].split(".")[0]
                if stack_tag == tag_lower:
                    self.stack = self.stack[:i]
                    break


class DOMSimilarityEngine:
    """Fuzzy clustering engine for grouping pages by DOM layout similarity."""

    def __init__(self, similarity_threshold: float = 0.92, visit_limit: int = 3) -> None:
        self.similarity_threshold = similarity_threshold
        self.visit_limit = visit_limit
        # template_id -> path_set
        self.templates: dict[str, set[str]] = {}
        # template_id -> set of exact MD5 hashes matching this template
        self.exact_hashes: dict[str, set[str]] = {}
        # template_id -> total visits recorded
        self.visit_counts: dict[str, int] = {}

    def get_template_id(self, html_content: str) -> tuple[str, float]:
        """Classify page HTML into a template ID.

        Uses O(1) exact hash match fast-path, falling back to Jaccard path similarity comparison.
        """
        extractor = DOMPathExtractor()
        with contextlib.suppress(Exception):
            extractor.feed(html_content)

        paths = extractor.paths
        if not paths:
            return "empty", 0.0

        # Compute exact hash of the unique paths set
        sorted_paths = sorted(paths)
        combined = "|".join(sorted_paths)
        exact_hash = hashlib.md5(combined.encode("utf-8")).hexdigest()

        # 1. Fast-Path: O(1) exact match lookup
        for tid, hash_set in self.exact_hashes.items():
            if exact_hash in hash_set:
                return tid, 1.0

        # 2. Slow-Path: Calculate Jaccard similarity against templates
        best_tid = None
        best_sim = 0.0

        for tid, ref_paths in self.templates.items():
            intersection = len(paths.intersection(ref_paths))
            union = len(paths.union(ref_paths))
            sim = intersection / union if union > 0 else 0.0
            if sim > best_sim:
                best_sim = sim
                best_tid = tid

        # If it fits within an existing layout, cache it
        if best_sim >= self.similarity_threshold and best_tid is not None:
            self.exact_hashes[best_tid].add(exact_hash)
            return best_tid, best_sim

        # Otherwise, create a new template category
        new_tid = f"tpl_{len(self.templates) + 1}"
        self.templates[new_tid] = paths
        self.exact_hashes[new_tid] = {exact_hash}
        self.visit_counts[new_tid] = 0
        return new_tid, 1.0

    def record_visit(self, template_id: str) -> int:
        """Increment visit count for a template."""
        if template_id not in self.visit_counts:
            self.visit_counts[template_id] = 0
        self.visit_counts[template_id] += 1
        return self.visit_counts[template_id]

    def should_gate_links(self, template_id: str) -> bool:
        """Determine if further links should be skipped for this layout template."""
        if template_id == "empty":
            return False
        return self.visit_counts.get(template_id, 0) > self.visit_limit
