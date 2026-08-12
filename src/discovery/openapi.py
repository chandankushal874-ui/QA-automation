"""OpenAPI/Swagger specification auto-discovery and parsing module.

Features:
- Search and fetch openapi.json, swagger.json, etc.
- Parse JSON/YAML specs to extract routes and http methods.
- Populate registry as EndpointSource.OPENAPI_SPEC.

Spec reference: master-build-prompt §3, Step M
"""

from __future__ import annotations

import json
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from src.core.endpoint_registry import EndpointRegistry, EndpointSource

# Common spec file locations to search
OPENAPI_SPEC_LOCATIONS: list[str] = [
    "/openapi.json",
    "/swagger.json",
    "/api-docs",
    "/swagger/v1/swagger.json",
    "/api/swagger.json",
    "/api/openapi.json",
    "/v1/openapi.json",
    "/v2/openapi.json",
    "/docs/openapi.json",
]


def parse_openapi_spec_content(spec_text: str) -> list[tuple[str, str]]:
    """Parse OpenAPI/Swagger content (JSON or YAML) and return routes.

    Returns:
        List of tuples: (route_path, http_method)
    """
    routes: list[tuple[str, str]] = []

    # Try parsing as JSON first
    try:
        data = json.loads(spec_text)
    except json.JSONDecodeError:
        # Fallback to YAML
        try:
            data = yaml.safe_load(spec_text)
        except Exception:
            return []

    if not isinstance(data, dict):
        return []

    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        return []

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method in methods:
            method_upper = method.upper()
            if method_upper in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                routes.append((path, method_upper))

    return routes


async def fetch_and_parse_spec(spec_url: str, registry: EndpointRegistry, base_url: str) -> int:
    """Fetch an OpenAPI spec URL, parse it, and register new endpoints."""
    new_count = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(spec_url)
            if resp.status_code == 200:
                routes = parse_openapi_spec_content(resp.text)
                for path, method in routes:
                    # Resolve path
                    resolved = urljoin(base_url, path)

                    # Ensure same domain
                    parsed_resolved = urlparse(resolved)
                    parsed_base = urlparse(base_url)
                    if parsed_resolved.netloc and parsed_resolved.netloc != parsed_base.netloc:
                        continue

                    _, is_new = registry.add(
                        url=resolved,
                        method=method,
                        source=EndpointSource.OPENAPI_SPEC,
                        discovered_on_page=spec_url,
                    )
                    if is_new:
                        new_count += 1
    except Exception:
        pass

    return new_count


async def auto_discover_openapi_specs(base_url: str, registry: EndpointRegistry) -> int:
    """Brute force check common Swagger/OpenAPI endpoints and import them if found."""
    new_count = 0
    for location in OPENAPI_SPEC_LOCATIONS:
        spec_url = urljoin(base_url, location)
        added = await fetch_and_parse_spec(spec_url, registry, base_url)
        if added > 0:
            new_count += added
            # If we found a valid OpenAPI spec, we can typically stop searching
            break
    return new_count
