"""Fixture App — A deliberately tricky web app for testing the platform.

This app contains intentional traps, bugs, and edge cases:
- Login-gated section with multiple auth methods (form, Bearer token, OAuth mock)
- SPA page with client-side-only routing (no URL change on navigation)
- Infinite-scroll page
- Pagination trap (?page=N that goes to 9999)
- Intentionally slow endpoint
- Intermittently-500ing endpoint
- "Delete Account" button (must never be auto-clicked)
- Hidden API endpoints (only referenced in JS bundles)
- Admin panel at a non-obvious path
- GraphQL endpoint with introspection enabled
- Endpoints that emit various console errors (CORS, CSP, unhandled rejections)
- Debug endpoint that leaks stack traces
- Weak auth endpoint accepting default credentials
"""

from __future__ import annotations

import asyncio
import random
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# ─── App setup ───

BASE_DIR = Path(__file__).parent
app = FastAPI(title="Fixture App — Deliberately Tricky Test Target", version="1.0.0")

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Intentionally restrictive CORS for testing CORS error detection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://allowed-origin-only.example.com"],
    allow_methods=["GET"],
    allow_headers=["X-Custom-Header"],
)

# ─── Fake data ───

USERS_DB: dict[str, dict[str, str]] = {
    "admin": {"password": "admin", "role": "admin"},
    "user": {"password": "password", "role": "user"},
    "demo": {"password": "demo", "role": "viewer"},
    "test": {"password": "test", "role": "user"},
}

SESSIONS: dict[str, dict[str, str]] = {}

PRODUCTS = [
    {"id": i, "name": f"Product {i}", "price": round(random.uniform(5, 500), 2), "category": random.choice(["Electronics", "Books", "Clothing", "Home"])}
    for i in range(1, 201)
]


# ─── Auth helpers ───


def verify_session(request: Request) -> dict[str, str] | None:
    """Check for a valid session cookie or Bearer token."""
    # Check session cookie
    session_id = request.cookies.get("session_id")
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]

    # Check Bearer token
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token in SESSIONS:
            return SESSIONS[token]

    return None


def require_auth(request: Request) -> dict[str, str]:
    """Dependency that requires authentication."""
    user = verify_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ─── Public pages ───


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    """Home page with links to various sections."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> Response:
    """Simple about page."""
    return templates.TemplateResponse(request=request, name="about.html")


@app.get("/contact", response_class=HTMLResponse)
async def contact(request: Request) -> Response:
    """Contact page."""
    return templates.TemplateResponse(request=request, name="contact.html")


# ─── Login / Auth pages ───


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Login form page."""
    return templates.TemplateResponse(request=request, name="login.html")


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def api_login(body: LoginRequest, response: Response) -> dict[str, Any]:
    """API login endpoint — returns session token."""
    user = USERS_DB.get(body.username)
    if not user or user["password"] != body.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session_id = f"sess_{body.username}_{int(time.time())}"
    SESSIONS[session_id] = {"username": body.username, "role": user["role"]}

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="lax",
    )

    return {"token": session_id, "user": body.username, "role": user["role"]}


@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response) -> dict[str, str]:
    """Logout and clear session."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in SESSIONS:
        del SESSIONS[session_id]
    response.delete_cookie("session_id")
    return {"status": "logged_out"}


# ─── OAuth mock (for testing OAuth interception) ───


@app.get("/oauth/authorize")
async def oauth_authorize(
    client_id: str = Query(default="test_client"),
    redirect_uri: str = Query(default="/oauth/callback"),
    state: str = Query(default="random_state"),
) -> Response:
    """Mock OAuth authorization — redirects with a code."""
    return Response(
        status_code=302,
        headers={"Location": f"{redirect_uri}?code=mock_auth_code_12345&state={state}"},
    )


@app.get("/oauth/callback")
async def oauth_callback(code: str = Query(...), state: str = Query(default="")) -> dict[str, str]:
    """Mock OAuth callback — exchanges code for token."""
    if code == "mock_auth_code_12345":
        token = f"oauth_token_{int(time.time())}"
        SESSIONS[token] = {"username": "oauth_user", "role": "user"}
        return {"access_token": token, "token_type": "Bearer", "expires_in": "3600"}
    raise HTTPException(status_code=400, detail="Invalid authorization code")


# ─── Protected section (requires auth) ───


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict[str, str] = Depends(require_auth)) -> Response:
    """Auth-gated dashboard page."""
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"user": user})


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, user: dict[str, str] = Depends(require_auth)) -> Response:
    """Auth-gated profile page."""
    return templates.TemplateResponse(request=request, name="profile.html", context={"user": user})


# ─── SPA page with client-side-only routing ───


@app.get("/spa", response_class=HTMLResponse)
async def spa_page(request: Request) -> Response:
    """SPA page — all navigation is client-side, URL doesn't change."""
    return templates.TemplateResponse(request=request, name="spa.html")


# ─── Infinite scroll page ───


@app.get("/infinite-scroll", response_class=HTMLResponse)
async def infinite_scroll_page(request: Request) -> Response:
    """Page with infinite scroll that keeps loading content."""
    return templates.TemplateResponse(request=request, name="infinite_scroll.html")


@app.get("/api/feed")
async def api_feed(page: int = Query(default=1, ge=1), limit: int = Query(default=20, le=50)) -> dict[str, Any]:
    """API for infinite scroll — always returns data, never ends."""
    items = [
        {
            "id": (page - 1) * limit + i,
            "title": f"Post #{(page - 1) * limit + i}",
            "content": f"This is auto-generated content for infinite scroll testing. Page {page}, item {i}.",
            "timestamp": datetime.now(UTC).isoformat(),
        }
        for i in range(1, limit + 1)
    ]
    return {
        "items": items,
        "page": page,
        "has_more": True,  # Always true — infinite!
        "total": 999999,
    }


# ─── Pagination trap ───


@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request) -> Response:
    """Products page with pagination trap."""
    return templates.TemplateResponse(request=request, name="products.html")


@app.get("/api/products")
async def api_products(
    page: int = Query(default=1, ge=1, le=9999),
    limit: int = Query(default=20, le=50),
) -> dict[str, Any]:
    """Products API — pagination goes up to page 9999 (trap!)."""
    start = (page - 1) * limit
    end = start + limit
    page_products = PRODUCTS[start:end] if start < len(PRODUCTS) else []

    return {
        "items": page_products,
        "page": page,
        "total_pages": 9999,  # Trap: claims 9999 pages even though only ~10 have data
        "has_next": page < 9999,
        "has_prev": page > 1,
    }


# ─── Intentionally slow endpoint ───


@app.get("/api/slow")
async def slow_endpoint() -> dict[str, str]:
    """Intentionally slow — takes 5-10 seconds to respond."""
    delay = random.uniform(5.0, 10.0)
    await asyncio.sleep(delay)
    return {"status": "ok", "delay_seconds": str(round(delay, 2))}


# ─── Intermittently 500-ing endpoint ───


@app.get("/api/unstable")
async def unstable_endpoint() -> dict[str, str]:
    """Fails with 500 roughly 40% of the time."""
    if random.random() < 0.4:
        raise HTTPException(status_code=500, detail="Random internal server error — this is intentional!")
    return {"status": "ok", "message": "Got lucky this time!"}


# ─── DELETE ACCOUNT — must never be auto-clicked ───


@app.delete("/api/account/delete")
async def delete_account(user: dict[str, str] = Depends(require_auth)) -> dict[str, str]:
    """DANGEROUS: Delete user account. Must never be auto-triggered by the crawler."""
    # In a real app this would delete the user
    return {"status": "deleted", "user": user["username"], "warning": "ACCOUNT PERMANENTLY DELETED"}


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: dict[str, str] = Depends(require_auth)) -> Response:
    """Settings page with the dangerous Delete Account button."""
    return templates.TemplateResponse(request=request, name="settings.html", context={"user": user})


# ─── Hidden API endpoints (only referenced in JS bundles) ───


@app.get("/api/internal/config")
async def internal_config() -> dict[str, Any]:
    """Hidden config endpoint — not linked in UI, only in JS."""
    return {
        "feature_flags": {"new_ui": True, "beta_search": False, "dark_mode": True},
        "api_version": "2.1.0",
        "debug_mode": True,
    }


@app.get("/api/internal/users")
async def internal_users(user: dict[str, str] = Depends(require_auth)) -> list[dict[str, str]]:
    """Hidden users list — admin only, referenced in JS bundle."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return [{"username": u, "role": d["role"]} for u, d in USERS_DB.items()]


@app.get("/api/internal/metrics")
async def internal_metrics() -> dict[str, Any]:
    """Hidden metrics endpoint — no auth required (intentional security issue)."""
    return {
        "uptime_seconds": int(time.time()),
        "total_requests": random.randint(1000, 50000),
        "active_sessions": len(SESSIONS),
        "error_rate": round(random.uniform(0.01, 0.15), 3),
        "db_connection_pool": {"active": 5, "idle": 15, "max": 20},
    }


# ─── Admin panel at non-obvious path ───


@app.get("/internal/admin-panel", response_class=HTMLResponse)
async def admin_panel(request: Request) -> Response:
    """Admin panel at a non-obvious URL — should be discovered by brute-force."""
    return templates.TemplateResponse(request=request, name="admin.html")


# ─── GraphQL endpoint with introspection ───


GRAPHQL_SCHEMA = {
    "types": [
        {"name": "User", "fields": ["id", "username", "email", "role", "createdAt"]},
        {"name": "Product", "fields": ["id", "name", "price", "category", "inStock"]},
        {"name": "Order", "fields": ["id", "userId", "products", "total", "status"]},
    ],
    "queries": ["user(id: ID!)", "users", "product(id: ID!)", "products(category: String)", "orders(userId: ID)"],
    "mutations": ["createUser(input: CreateUserInput!)", "updateProduct(id: ID!, input: UpdateProductInput!)", "deleteOrder(id: ID!)"],
}


@app.post("/graphql")
@app.get("/graphql")
async def graphql_endpoint(request: Request) -> dict[str, Any]:
    """Mock GraphQL endpoint with introspection support."""
    try:
        if request.method == "GET":
            body: dict[str, Any] = {"query": request.query_params.get("query", "")}
        else:
            body = await request.json()
    except Exception:
        body = {}

    query = body.get("query", "")

    # Handle introspection
    if "__schema" in query or "IntrospectionQuery" in query:
        return {
            "data": {
                "__schema": {
                    "types": GRAPHQL_SCHEMA["types"],
                    "queryType": {"name": "Query"},
                    "mutationType": {"name": "Mutation"},
                    "queries": GRAPHQL_SCHEMA["queries"],
                    "mutations": GRAPHQL_SCHEMA["mutations"],
                }
            }
        }

    # Handle simple queries
    if "users" in query.lower():
        return {"data": {"users": [{"id": "1", "username": "admin", "role": "admin"}]}}

    if "products" in query.lower():
        return {"data": {"products": PRODUCTS[:5]}}

    return {"data": None, "errors": [{"message": "Query not recognized", "query": query}]}


# ─── Endpoints that emit console errors ───


@app.get("/console-errors", response_class=HTMLResponse)
async def console_errors_page(request: Request) -> Response:
    """Page that intentionally triggers various console errors."""
    return templates.TemplateResponse(request=request, name="console_errors.html")


@app.get("/api/cors-test")
async def cors_test() -> dict[str, str]:
    """Endpoint that will trigger CORS errors when called from certain origins."""
    return {"message": "This should trigger CORS errors from the console-errors page"}


# ─── Debug endpoint that leaks stack traces ───


@app.get("/debug/error")
async def debug_error() -> dict[str, str]:
    """Debug endpoint that intentionally leaks a full stack trace."""
    try:
        # Intentional error
        result: dict[str, str] = {}
        _ = result["nonexistent_key"]  # type: ignore[assignment]
        return {"status": "ok"}
    except KeyError:
        return {
            "error": "Internal error",
            "stack_trace": traceback.format_exc(),
            "env": {  # Intentional info leak
                "python_version": "3.12",
                "debug_mode": "true",
                "database_url": "postgresql://cbtp:LEAKED_PASSWORD@db:5432/cbtp",  # Intentional leak
            },
        }


@app.get("/debug/info")
async def debug_info() -> dict[str, Any]:
    """Debug info endpoint — leaks environment details."""
    return {
        "hostname": "fixture-app-server-01",
        "python_path": str(Path(__file__).parent),
        "env_vars": {
            "DATABASE_URL": "postgresql://cbtp:secret@localhost/cbtp",
            "SECRET_KEY": "super_secret_key_leaked_intentionally",
            "API_KEY": "sk-test-leaked-key-12345",
        },
        "routes": [route.path for route in app.routes],
    }


# ─── Weak auth endpoint (accepts default creds) ───


@app.post("/api/auth/quick-login")
async def quick_login(response: Response) -> dict[str, str]:
    """Weak auth — no credentials needed, auto-grants access. Security flaw."""
    token = f"weak_sess_{int(time.time())}"
    SESSIONS[token] = {"username": "guest", "role": "viewer"}
    response.set_cookie(key="session_id", value=token, httponly=False)  # httpOnly=false intentionally
    return {"token": token, "warning": "This endpoint has no authentication — intentional security flaw"}


# ─── OpenAPI / Swagger (auto-generated by FastAPI) ───
# FastAPI already serves /docs (Swagger UI) and /openapi.json by default


# ─── Robots.txt with disallowed paths ───


@app.get("/robots.txt")
async def robots_txt() -> Response:
    """Robots.txt with interesting disallowed paths."""
    content = """User-agent: *
Allow: /
Disallow: /internal/
Disallow: /debug/
Disallow: /api/internal/
Disallow: /admin-backup/
Disallow: /secret-docs/

Sitemap: http://localhost:8090/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


# ─── Sitemap.xml ───


@app.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    """Sitemap with all public pages."""
    urls = ["/", "/about", "/contact", "/login", "/products", "/infinite-scroll", "/spa", "/console-errors"]
    content = '<?xml version="1.0" encoding="UTF-8"?>\n'
    content += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url in urls:
        content += f"  <url><loc>http://localhost:8090{url}</loc></url>\n"
    content += "</urlset>"
    return Response(content=content, media_type="application/xml")


# ─── Health check ───


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "app": "fixture-app"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
