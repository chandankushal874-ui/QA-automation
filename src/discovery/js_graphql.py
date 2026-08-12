"""JS bundle endpoint mining and GraphQL introspection module.

Features:
- Search JS bundle contents for hardcoded endpoints (fetch, axios, paths)
- Execute GraphQL introspection queries and parse types/queries/mutations/subscriptions

Spec reference: master-build-prompt §3, Step I
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from src.core.endpoint_registry import EndpointRegistry, EndpointSource

# Introspection query for GraphQL
GRAPHQL_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind
      name
      fields {
        name
        description
      }
    }
  }
}
"""


def mine_endpoints_from_js(js_content: str) -> set[str]:
    """Mine endpoints and paths from Javascript bundle code using regex heuristics."""
    found_paths: set[str] = []

    # 1. Matches common path prefixes inside quotes (double, single, backtick)
    # Prefixes: /api, /internal, /debug, /graphql, /oauth, /login, /logout, /health, /metrics
    path_regex = r'(?:["\'`])((?:/api|/internal|/debug|/graphql|/oauth|/login|/logout|/health|/metrics)(?:/[a-zA-Z0-9_\-\./\?&%=]*)?)'
    found_paths.extend(re.findall(path_regex, js_content))

    # 2. Matches fetch/axios calls: fetch('/something') or axios.get('/something')
    ajax_regex = r'(?:fetch|axios(?:\.get|\.post|\.put|\.delete)?)\s*\(\s*["\'`]((?:/[a-zA-Z0-9_\-\./]*)|(?:https?://[a-zA-Z0-9_\-\.\:\/]*))["\'`]'
    found_paths.extend(re.findall(ajax_regex, js_content))

    # 3. Matches object configurations: url: '/something' or endpoint: '/something'
    config_regex = (
        r'(?:url|path|endpoint)\s*:\s*["\'`]((?:/[a-zA-Z0-9_\-\./]*)|(?:https?://[a-zA-Z0-9_\-\.\:\/]*))["\'`]'
    )
    found_paths.extend(re.findall(config_regex, js_content))

    # Clean and filter results
    cleaned = set()
    for path in found_paths:
        path = path.strip()
        if not path or path.startswith("//") or path.lower().startswith("javascript:"):
            continue
        # Strip trailing quote remnants if any
        path = re.sub(r'["\'`]$', "", path)
        cleaned.add(path)

    return cleaned


async def process_js_endpoint_mining(js_url: str, registry: EndpointRegistry, base_url: str) -> int:
    """Fetch a JS bundle URL and mine all hardcoded endpoints into the registry."""
    new_count = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(js_url)
            if resp.status_code == 200:
                paths = mine_endpoints_from_js(resp.text)
                for path in paths:
                    # Resolve path
                    resolved = urljoin(base_url, path)

                    # Skip out-of-scope URLs
                    parsed_resolved = urlparse(resolved)
                    parsed_base = urlparse(base_url)
                    if parsed_resolved.netloc and parsed_resolved.netloc != parsed_base.netloc:
                        # Skip if domain doesn't match base
                        continue

                    # Standardize method heuristic
                    method = "GET"
                    if "delete" in path.lower():
                        method = "DELETE"
                    elif (
                        "update" in path.lower()
                        or "edit" in path.lower()
                        or "create" in path.lower()
                        or "add" in path.lower()
                    ):
                        method = "POST"

                    _, is_new = registry.add(
                        url=resolved,
                        method=method,
                        source=EndpointSource.JS_ANALYSIS,
                        discovered_on_page=js_url,
                    )
                    if is_new:
                        new_count += 1
    except Exception:
        pass

    return new_count


def parse_graphql_schema(schema_data: dict[str, Any]) -> list[tuple[str, str]]:
    """Parse GraphQL introspection types and return list of (field_name, operation_type) tuples.

    Checks standard Query/Mutation/Subscription fields and also tolerates custom mock schemas.
    """
    findings = []

    # 1. Custom mock schemas support (as in our fixture mock)
    if "queries" in schema_data:
        for q in schema_data["queries"]:
            name = q.split("(")[0].strip()
            findings.append((name, "query"))
    if "mutations" in schema_data:
        for m in schema_data["mutations"]:
            name = m.split("(")[0].strip()
            findings.append((name, "mutation"))

    # 2. Standard specification parsing
    query_type = schema_data.get("queryType", {}).get("name", "Query") if schema_data.get("queryType") else "Query"
    mutation_type = (
        schema_data.get("mutationType", {}).get("name", "Mutation") if schema_data.get("mutationType") else "Mutation"
    )
    subscription_type = (
        schema_data.get("subscriptionType", {}).get("name", "Subscription")
        if schema_data.get("subscriptionType")
        else "Subscription"
    )

    types = schema_data.get("types", [])
    for t in types:
        type_name = t.get("name")
        fields = t.get("fields") or []
        if not fields:
            continue

        op_type = None
        if type_name == query_type:
            op_type = "query"
        elif type_name == mutation_type:
            op_type = "mutation"
        elif type_name == subscription_type:
            op_type = "subscription"

        if op_type:
            for f in fields:
                field_name = f if isinstance(f, str) else f.get("name", "")
                if field_name:
                    findings.append((field_name, op_type))

    return findings


async def execute_graphql_introspection(graphql_url: str, registry: EndpointRegistry) -> int:
    """Send introspection query to GraphQL endpoint and register all queries/mutations."""
    new_count = 0
    payload = {"query": GRAPHQL_INTROSPECTION_QUERY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try POST first (standard)
            resp = await client.post(graphql_url, json=payload)

            # Fallback to GET if POST fails
            if resp.status_code != 200:
                resp = await client.get(graphql_url, params={"query": GRAPHQL_INTROSPECTION_QUERY})

            if resp.status_code == 200:
                data = resp.json()
                schema = data.get("data", {}).get("__schema", {})

                # Check custom/direct data formats or mock wraps
                if not schema and "queryType" in data:
                    schema = data  # direct wrap

                operations = parse_graphql_schema(schema)

                for op_name, op_type in operations:
                    # Construct virtual endpoints
                    virtual_url = f"{graphql_url}?{op_type}={op_name}"

                    method = "POST" if op_type == "mutation" else "GET"

                    _, is_new = registry.add(
                        url=virtual_url,
                        method=method,
                        source=EndpointSource.GRAPHQL_INTROSPECTION,
                        discovered_on_page=graphql_url,
                    )
                    if is_new:
                        new_count += 1
    except Exception:
        pass

    return new_count
