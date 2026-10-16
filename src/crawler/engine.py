"""Core crawling and execution engine.

Orchestrates the crawl loop across multiple browsers, implements
destructive action gating, journey replaying, bug assertion, and issue triage.

Spec reference: master-build-prompt §3, Steps O, P, Q, R, S
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from src.analysis.accessibility import AccessibilityAuditor
from src.analysis.api_tester import APITester
from src.analysis.fix_engine import OpenRouterFixer, StaticAnalyzer, VerificationEngine
from src.analysis.visual_regression import VisualRegressionEngine
from src.auth.bypass import AuthBypassEngine, AuthContext
from src.browser.factory import BrowserConfig, BrowserContextFactory, InstrumentedPage
from src.core.console_capture import ConsoleCategory
from src.core.dom_similarity import DOMSimilarityEngine
from src.core.endpoint_registry import EndpointRegistry, EndpointSource, EndpointStatus, EndpointType
from src.core.frontier import FrontierNode, RedisFrontier
from src.discovery.js_graphql import execute_graphql_introspection, process_js_endpoint_mining
from src.discovery.mining import (
    detect_llms_txt,
    discover_static_seed_endpoints,
    mine_html_dom,
    parse_robots_txt,
    verify_sitemap_urls,
)
from src.discovery.openapi import auto_discover_openapi_specs
from src.discovery.prioritization import calculate_priority_score
from src.discovery.spa_graph import LIVE_DOM_HASH_JS, SPAGraph, SPAState, SPATransition
from src.models.database import CrawlState, Issue, IssueCategory, IssueOccurrence, IssueSeverity, Job, JobStatus

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Destructive patterns for gating (Step P)
DESTRUCTIVE_PATH_PATTERNS = [
    r"/delete",
    r"/destroy",
    r"/remove",
    r"/purge",
    r"/format",
    r"/wipe",
    r"/clear",
    r"/reset",
    r"/drop",
]

DESTRUCTIVE_SELECTOR_PATTERNS = [
    r"delete",
    r"destroy",
    r"purge",
    r"remove",
    r"clear",
    r"wipe",
    r"format",
    r"reset",
    r"drop",
]


def _find_test_file(source_file: str, repo_dir: str) -> str | None:
    """Return the pytest file that corresponds to *source_file*, or None.

    Searches the ``tests/`` directory inside *repo_dir* for a file named
    ``test_<stem>.py`` where ``<stem>`` is the base name of *source_file*
    without its extension (e.g. ``fix_engine.py`` → ``test_fix_engine.py``).
    """
    stem = os.path.splitext(os.path.basename(source_file))[0]
    candidate_name = f"test_{stem}.py"
    tests_dir = os.path.join(repo_dir, "tests")
    if os.path.isdir(tests_dir):
        candidate = os.path.join(tests_dir, candidate_name)
        if os.path.isfile(candidate):
            return candidate
    # Broader walk in case tests are nested
    for root, _dirs, files in os.walk(repo_dir):
        if candidate_name in files:
            return os.path.join(root, candidate_name)
    return None


def is_destructive_action(
    url: str, method: str = "GET", selector: str | None = None, element_text: str | None = None
) -> bool:
    """Evaluate if an action is potentially destructive to protect test targets (Step P)."""
    # 1. Method check (DELETE is always destructive-risk)
    if method.upper() == "DELETE":
        return True

    # 2. Path check
    parsed = urlparse(url)
    path = parsed.path.lower()
    for pattern in DESTRUCTIVE_PATH_PATTERNS:
        if re.search(pattern, path):
            return True

    # 3. Selector check
    if selector:
        selector_lower = selector.lower()
        for pattern in DESTRUCTIVE_SELECTOR_PATTERNS:
            if re.search(pattern, selector_lower):
                return True

    # 4. Element text check (e.g. button label)
    if element_text:
        text_lower = element_text.lower()
        destructive_words = {"delete", "destroy", "format", "wipe", "purge", "remove", "reset"}
        if any(word in text_lower for word in destructive_words) and (
            "cookie" not in text_lower and "filter" not in text_lower
        ):
            # Safe words bypass (e.g. "delete cookie consent" or "remove filter" are usually safe)
            return True

    return False


class CrawlEngine:
    """The central crawling execution manager (Phase 3)."""

    def __init__(
        self, db_session: Session, redis_client: Any, job_id: str | uuid.UUID, allow_destructive: bool = False
    ) -> None:
        self.db = db_session
        self.redis = redis_client

        # Convert job_id to uuid.UUID object for database query compatibility (e.g. on SQLite)
        import uuid

        if isinstance(job_id, str):
            self.job_uuid = uuid.UUID(job_id)
        else:
            self.job_uuid = job_id

        self.job_id = str(job_id)
        self.allow_destructive = allow_destructive
        self.spa_graph = SPAGraph()

        # Load Job FIRST so owner_id is available for tenant isolation.
        # (Previously frontier was built before job load → owner_id always None
        # and User A / User B frontiers shared the "legacy" namespace.)
        self.job = db_session.query(Job).filter_by(id=self.job_uuid).first()
        if not self.job:
            raise ValueError(f"Job {job_id} not found in database")

        from src.core.tenant_isolation import assert_job_owner_isolation, resolve_job_owner_id

        _owner = resolve_job_owner_id(self.job)
        assert_job_owner_isolation(self.job)
        self.owner_id = _owner
        self.frontier = RedisFrontier(
            redis_client,
            job_id=self.job_id,
            owner_id=str(_owner) if _owner else None,
        )

        self.registry = EndpointRegistry(base_url=self.job.url, scope=self.job.scope)
        self.max_pages = self.job.max_pages
        self.max_depth = self.job.max_depth
        self.max_time = self.job.max_time_seconds

        self.start_time: datetime | None = None
        self.pages_visited_count = 0
        self.issues_dedup: dict[str, Issue] = {}  # fingerprint -> Issue database model
        self.a11y_auditor = AccessibilityAuditor()  # Accessibility auditor (axe-core)
        self.a11y_results: dict[str, Any] = {}  # url -> A11yReport
        # Tenant isolation: per-owner paths (User A baselines/reports never mix with User B)
        from src.core.tenant_isolation import apply_job_isolation_paths

        iso = (self.job.config or {}).get("isolation") or {}
        self._iso_paths = iso.get("paths") if isinstance(iso, dict) else None
        if not self._iso_paths:
            self._iso_paths = apply_job_isolation_paths(self.job)
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()
        baselines = self._iso_paths.get("baselines") if self._iso_paths else None
        self.visual_engine = VisualRegressionEngine(
            # Job-scoped fallback: a re-test of the same URL never reuses an old baseline
            baselines_dir=baselines or f"baselines/{self.job_id}",
            threshold=0.1,
        )
        self.dom_similarity = DOMSimilarityEngine()

        # Headed mode: job config "show_browser" pops a visible browser window
        # on the server for this run (one window per job/browser engine).
        self.headless = not bool((self.job.config or {}).get("show_browser"))

    async def _ensure_healthy_page(
        self,
        browser: str,
        factory: BrowserContextFactory,
        active_pages: dict[str, InstrumentedPage],
        storage_state: dict[str, Any] | None = None,
        initial_url: str = "",
    ) -> InstrumentedPage:
        """Ensure a healthy browser page exists for the given browser engine.

        If the existing page is closed/crashed, recreate it. This is the
        critical resilience fix that prevents the crawl from stalling when
        Playwright crashes mid-crawl (the 38/52 pages bug).
        """
        page = active_pages.get(browser)

        # Check if the existing page is still alive
        if page is not None:
            try:
                # Probe the page — if it's closed/crashed, this raises
                _ = page.page.url  # property access raises if page is closed
                if page.page.is_closed():
                    raise RuntimeError("Page is closed")
                return page
            except Exception as e:
                logger.warning(f"Browser page for {browser} is dead ({e}). Recreating a fresh page...")
                # Close the dead page reference (best-effort)
                with contextlib.suppress(Exception):
                    await page.close()
                active_pages.pop(browser, None)

        # Create a fresh page — artifact paths are tenant-scoped when isolation is active
        _p = getattr(self, "_iso_paths", None) or {}
        config = BrowserConfig(
            engine=browser,
            headless=self.headless,
            storage_state=storage_state,
            ignore_https_errors=True,
            enable_tracing=True,
            trace_dir=_p.get("traces") or f"reports/traces/{self.job_id}",
            job_id=str(self.job_id),
            record_video_dir=_p.get("videos") or f"reports/videos/{self.job_id}",
            record_har_path=(
                f"{_p['har']}/{browser}_record.har"
                if _p.get("har")
                else f"reports/har/{self.job_id}/{browser}_record.har"
            ),
        )
        new_page = await factory.create_page(
            config,
            endpoint_registry=self.registry,
            initial_url=initial_url,
        )
        active_pages[browser] = new_page
        logger.info(f"Created fresh browser page for {browser}")
        return new_page

    async def _crawl_with_retry(
        self,
        url: str,
        method: str,
        depth: int,
        browser: str,
        factory: BrowserContextFactory,
        active_pages: dict[str, InstrumentedPage],
        parent_node: FrontierNode,
        storage_state: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> tuple[bool, str]:
        """Crawl a URL on a browser with automatic page recreation + retry.

        This wraps crawl_url_on_browser with resilience:
        - If the page is dead/crashed, recreate it before retrying
        - Retry up to max_retries times on failure
        - Prevents the cascade failure that stalled crawls at 38/52 pages
        """
        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                # Ensure we have a healthy page (recreate if crashed)
                page = await self._ensure_healthy_page(
                    browser=browser,
                    factory=factory,
                    active_pages=active_pages,
                    storage_state=storage_state,
                    initial_url=url,
                )

                # Reset console capture for the new URL
                from src.core.console_capture import ConsoleCapture

                page.console = ConsoleCapture(url=url, browser=browser)

                res = await self.crawl_url_on_browser(
                    url=url,
                    method=method,
                    depth=depth,
                    browser_engine=browser,
                    page=page,
                    parent_node=parent_node,
                )
                # Success — return result
                return res
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Crawl attempt {attempt}/{max_retries} failed for {url} on {browser}: {e}")
                # Kill the broken page so _ensure_healthy_page recreates it next attempt
                broken = active_pages.pop(browser, None)
                if broken:
                    with contextlib.suppress(Exception):
                        await broken.close()
                if attempt < max_retries:
                    # Brief backoff before retry
                    await asyncio.sleep(2.0)
        # All retries exhausted
        return False, f"All {max_retries} attempts failed: {last_error}"

    async def execute(self) -> None:
        """Run the full autonomous cross-browser crawling engine loop."""
        self.start_time = datetime.now(UTC)
        self.job.status = JobStatus.RUNNING
        self.job.started_at = self.start_time
        self.db.commit()

        logger.info(f"Starting Crawl Job {self.job_id} on target {self.job.url}")
        try:
            await self._execute_inner()
        except Exception as e:
            logger.error(f"FATAL: Crawl engine crashed: {e}", exc_info=True)
            # Ensure job is marked as failed so frontend doesn't stay stuck at "running"
            try:
                self.job.status = JobStatus.FAILED
                self.job.completed_at = datetime.now(UTC)
                self.db.commit()
                logger.info(f"Job {self.job_id} marked FAILED due to crash")
            except Exception:
                pass
            # Best-effort partial report: whatever was crawled before the
            # crash must still be visible and downloadable for the user.
            try:
                from src.analysis.report_generator import generate_static_html_report

                issues = self.db.query(Issue).filter_by(job_id=self.job.id).all()
                report_path = generate_static_html_report(self.job_id, self.job, self.registry, issues)
                try:
                    import shutil

                    _p = getattr(self, "_iso_paths", None) or {}
                    tenant_report = _p.get("report_html")
                    if tenant_report and report_path and os.path.exists(report_path):
                        os.makedirs(os.path.dirname(tenant_report), exist_ok=True)
                        shutil.copy2(report_path, tenant_report)
                except Exception as copy_err:
                    logger.debug("Tenant partial-report mirror skipped: %s", copy_err)
                logger.info(f"Partial report generated for failed job at {report_path}")
            except Exception as rep_err:
                logger.error(f"Could not generate partial report for failed job: {rep_err}")
            raise

    async def _execute_inner(self) -> None:
        """Internal crawl execution — wrapped by execute() for crash safety."""

        # Check if we should resume a crawl
        is_resume = self.frontier.seen_count() > 0 or self.frontier.size() > 0
        self.crawl_delay = 1.5
        cancelled = False  # Track whether user requested cancellation

        if not is_resume:
            # Step 1: Discover seed/static endpoints (robots.txt, sitemap.xml) (Step H)
            _, robots_delay = await discover_static_seed_endpoints(self.job.url, self.registry)
            if robots_delay is not None:
                self.crawl_delay = robots_delay
            await auto_discover_openapi_specs(self.job.url, self.registry)

            # DeepSeek-parity: Active sitemap verification pass.
            # Probes every sitemap-declared URL with HEAD/GET and records
            # 4xx/5xx responses as NETWORK_FAILURE issues. This closes the
            # gap where sitemap URLs not linked from crawled pages were
            # never probed (DeepSeek found ~15 404s; passive crawl found 2).
            # The existing frontier/DOM-discovery flow is 100% intact —
            # this runs as a separate pre-crawl verification pass.
            try:
                logger.info("Running active sitemap URL verification pass...")
                sitemap_result = await verify_sitemap_urls(
                    base_url=self.job.url,
                    registry=self.registry,
                    db_session=self.db,
                    job_id=self.job.id,
                )
                logger.info(
                    f"Sitemap verification: {sitemap_result['verified']}/{sitemap_result['total_sitemap_urls']} "
                    f"OK, {sitemap_result['missing']} missing (404/4xx/5xx)"
                )
                # Store the summary on the job for the report generator
                self.job.config = self.job.config or {}
                self.job.config["sitemap_verification"] = sitemap_result
                self.db.commit()
            except Exception as e:
                logger.warning(f"Sitemap verification pass failed (non-fatal): {e}")

            # DeepSeek-parity: Detect /llms.txt (AI crawler instructions).
            try:
                has_llms = await detect_llms_txt(self.job.url, self.registry)
                if has_llms:
                    logger.info("llms.txt detected and registered")
            except Exception as e:
                logger.warning(f"llms.txt detection failed (non-fatal): {e}")

            # Initialize frontier for Phase 1
            seed_node = FrontierNode(
                url=self.job.url,
                method="GET",
                depth=0,
                priority_score=0.1,  # Highest priority
            )
            self.frontier.push(seed_node)
        else:
            logger.info("Resuming crawl from existing checkpoint in frontier...")
            self.frontier.requeue_processing()
            try:
                _, _, robots_delay = await parse_robots_txt(self.job.url)
                if robots_delay is not None:
                    self.crawl_delay = robots_delay
            except Exception:
                pass

        # Initialize browser context factory (Step F)
        async with BrowserContextFactory() as factory:
            active_browsers = [k for k, v in self.job.browser_matrix.items() if v]
            auth_state = None

            # ==========================================
            # PHASE 1: PUBLIC CRAWL (BEFORE LOGGING IN)
            # ==========================================
            logger.info("Starting Phase 1: Public Crawl (Before Logging In)")
            active_pages = {}
            try:
                while self.frontier.size() > 0:
                    # Refresh job status from DB to check for cancellation
                    self.db.refresh(self.job)
                    if self.job.status == "cancelled":
                        logger.info(f"Job {self.job_id} was cancelled by user.")
                        cancelled = True
                        break

                    # Budget guards (duration & page limits check)
                    elapsed = (datetime.now(UTC) - (self.start_time or datetime.now(UTC))).total_seconds()
                    if elapsed >= self.max_time:
                        logger.warning("Crawl time budget exceeded!")
                        break
                    if self.pages_visited_count >= self.max_pages:
                        logger.warning("Crawl page limit budget reached!")
                        break

                    node = self.frontier.pop_min()
                    if not node:
                        break

                    # Skip if already visited/skipped/blocked in registry
                    ep_match = self.registry.get(node.url, node.method)
                    if ep_match and ep_match.status == EndpointStatus.VISITED:
                        self.frontier.mark_done(node)
                        continue

                    # Step 3: Destructive-action gating (Step P)
                    if is_destructive_action(node.url, node.method) and not self.allow_destructive:
                        logger.warning(f"Blocking destructive action: {node.method} {node.url}")
                        self.registry.add(
                            url=node.url,
                            method=node.method,
                            source=EndpointSource.DOM_LINK,
                            requires_auth=node.requires_auth,
                        )
                        ep = self.registry.get(node.url, node.method)
                        if ep:
                            ep.status = EndpointStatus.BLOCKED
                        self.frontier.mark_done(node)
                        continue

                    # Step 4: Cross-browser sequential execution (Step Q)
                    results = []
                    for browser in active_browsers:
                        try:
                            res = await self._crawl_with_retry(
                                url=node.url,
                                method=node.method,
                                depth=node.depth,
                                browser=browser,
                                factory=factory,
                                active_pages=active_pages,
                                parent_node=node,
                                storage_state=None,  # Unauthenticated (Phase 1)
                            )
                            results.append(res)
                        except Exception as e:
                            logger.error(f"Failed to crawl {node.url} on {browser}: {e}")
                            results.append(e)

                    # Check results and extract discovered links
                    success = any(isinstance(r, tuple) and r[0] is True for r in results)

                    if success:
                        # Status code comes from the observed navigation response
                        # (recorded in crawl_url_on_browser), not a hardcoded 200.
                        self.registry.mark_visited(node.url, method=node.method)
                        self.pages_visited_count += 1
                    else:
                        self.registry.mark_failed(node.url, status_code=500)

                    self.frontier.mark_done(node)

                    # Log crawl updates in DB
                    self.job.pages_visited = self.pages_visited_count
                    self.job.pages_discovered = len(self.registry.get_all())
                    self.job.endpoints_discovered = len(self.registry.get_all())
                    self.db.commit()
            finally:
                # Close all active browser pages/windows from Phase 1
                for p in active_pages.values():
                    try:
                        await p.close()
                    except Exception as e:
                        logger.warning(f"Error closing browser: {e}")
                active_pages.clear()

            # If the user cancelled during Phase 1, stop here — don't run
            # API tests, auth bypass, Phase 2, or report generation.
            if cancelled:
                logger.info(f"Job {self.job_id} cancelled during Phase 1. Stopping early.")
                return

            # ==========================================
            # API ENDPOINT TESTING (Phase 1.5)
            # ==========================================
            logger.info("Running independent API endpoint tests...")
            api_tester = APITester()
            self.api_test_summary = await api_tester.test_discovered_apis(
                registry=self.registry,
                base_url=self.job.url,
            )

            # ==========================================
            # LOGIN BYPASS TRIGGER
            # ==========================================
            if self.job.has_credentials or self.job.auth_profile:
                logger.info("Phase 1 Complete. Triggering Authentication Bypass Phase (Step G)")
                # Launch a helper browser page to perform login bypass
                helper_page = await factory.create_page(
                    BrowserConfig(engine="chromium", headless=self.headless), endpoint_registry=self.registry
                )

                # Setup Auth Profile data
                credentials = {}
                login_url = self.job.url
                if self.job.auth_profile:
                    credentials = {
                        "username": self.job.auth_profile.username or "",
                        "password": self.job.auth_profile.password_encrypted or "",  # dev plaintext
                    }
                    login_url = self.job.auth_profile.login_url or self.job.url

                ctx = AuthContext(
                    target_url=self.job.url,
                    login_url=login_url,
                    credentials=credentials,
                    page=helper_page.page,
                    browser_context=helper_page.page.context,
                    is_localhost=self.job.url.startswith("http://localhost")
                    or self.job.url.startswith("http://127.0.0.1"),
                )

                bypass_engine = AuthBypassEngine()
                auth_state = await bypass_engine.authenticate(ctx)

                # Log attempts in AuthProfile
                if self.job.auth_profile:
                    self.job.auth_profile.is_authenticated = auth_state.is_authenticated
                    self.job.auth_profile.authenticated_at = auth_state.authenticated_at
                    self.job.auth_profile.auth_method = auth_state.method_used.value if auth_state.method_used else None
                    self.job.auth_profile.cookies = [
                        {"name": c["name"], "value": c["value"]} for c in auth_state.cookies
                    ]
                    self.job.auth_profile.storage_state = auth_state.storage_state
                    self.job.auth_profile.attempt_log = bypass_engine.get_attempt_log()
                    self.db.commit()

                await helper_page.close()

            # ==========================================
            # PHASE 2: AUTHENTICATED DEEP CRAWL
            # ==========================================
            if auth_state and auth_state.is_authenticated:
                logger.info("Starting Phase 2: Authenticated Crawl (Deep Gated Dashboard Testing)")

                # Reset frontier and identify gated/redirected endpoints from Phase 1
                self.frontier.clear()

                gated_endpoints = []
                for ep in self.registry.get_all():
                    url_lower = ep.url.lower()
                    # Check if the URL indicates an admin/dashboard page, or if it failed authentication in Phase 1
                    is_gated = any(
                        kw in url_lower for kw in ["dashboard", "admin", "console", "account", "settings", "manage"]
                    )
                    is_auth_error = ep.last_status_code in (401, 403) or ep.status == EndpointStatus.BLOCKED

                    if is_gated or is_auth_error:
                        ep.status = EndpointStatus.DISCOVERED  # Reset back to unvisited so it gets crawled again
                        gated_endpoints.append(ep)

                # Add dashboard redirect landing page or login URL as a primary seed
                dest_url = auth_state.destination_url or login_url
                dashboard_node = FrontierNode(url=dest_url, method="GET", depth=0, priority_score=0.1)
                self.frontier.push(dashboard_node)

                # Push other identified auth-gated endpoints to queue
                for ep in gated_endpoints:
                    node = FrontierNode(url=ep.url, method=ep.method, depth=1, priority_score=0.2)
                    self.frontier.push(node)

                # Active pages for Phase 2 authenticated loop
                active_pages = {}
                try:
                    while self.frontier.size() > 0:
                        # Refresh job status from DB to check for cancellation
                        self.db.refresh(self.job)
                        if self.job.status == "cancelled":
                            logger.info(f"Job {self.job_id} was cancelled by user.")
                            cancelled = True
                            break

                        # Budget guards (duration & page limits check)
                        elapsed = (datetime.now(UTC) - (self.start_time or datetime.now(UTC))).total_seconds()
                        if elapsed >= self.max_time:
                            logger.warning("Crawl time budget exceeded!")
                            break
                        if self.pages_visited_count >= self.max_pages:
                            logger.warning("Crawl page limit budget reached!")
                            break

                        node = self.frontier.pop_min()
                        if not node:
                            break

                        # Skip if already visited/skipped/blocked in registry
                        ep_match = self.registry.get(node.url, node.method)
                        if ep_match and ep_match.status == EndpointStatus.VISITED:
                            self.frontier.mark_done(node)
                            continue

                        # Step 3: Destructive Gating
                        if is_destructive_action(node.url, node.method) and not self.allow_destructive:
                            logger.warning(f"Blocking destructive action: {node.method} {node.url}")
                            self.registry.add(
                                url=node.url,
                                method=node.method,
                                source=EndpointSource.DOM_LINK,
                                requires_auth=node.requires_auth,
                            )
                            ep = self.registry.get(node.url, node.method)
                            if ep:
                                ep.status = EndpointStatus.BLOCKED
                            self.frontier.mark_done(node)
                            continue

                        # Step 4: Crawl sequentially using authenticated pages
                        results = []
                        for browser in active_browsers:
                            try:
                                res = await self._crawl_with_retry(
                                    url=node.url,
                                    method=node.method,
                                    depth=node.depth,
                                    browser=browser,
                                    factory=factory,
                                    active_pages=active_pages,
                                    parent_node=node,
                                    storage_state=auth_state.storage_state,  # Inject authenticated cookies
                                )
                                results.append(res)
                            except Exception as e:
                                logger.error(f"Failed to crawl {node.url} on {browser}: {e}")
                                results.append(e)

                        success = any(isinstance(r, tuple) and r[0] is True for r in results)
                        if success:
                            # Use the observed navigation status, not a hardcoded 200.
                            self.registry.mark_visited(node.url, method=node.method)
                            self.pages_visited_count += 1
                        else:
                            self.registry.mark_failed(node.url, status_code=500)

                        self.frontier.mark_done(node)

                        # Log crawl updates in DB
                        self.job.pages_visited = self.pages_visited_count
                        self.job.pages_discovered = len(self.registry.get_all())
                        self.job.endpoints_discovered = len(self.registry.get_all())
                        self.db.commit()
                finally:
                    # Close all active browser pages/windows from Phase 2
                    for p in active_pages.values():
                        try:
                            await p.close()
                        except Exception as e:
                            logger.warning(f"Error closing browser: {e}")

            # If the user cancelled during Phase 2, stop here.
            if cancelled:
                logger.info(f"Job {self.job_id} cancelled during Phase 2. Stopping early.")
                return

            # Step 5: Source-aware analysis & AI fix generation (Steps U-X)
            #
            # Two modes:
            #   * repo_dir available  -> full stack: source context + OpenRouter
            #                            AI fix + E2B pytest verification (verified).
            #   * no repo_dir         -> remote-website mode: OpenRouter AI fix
            #                            based on error/stack-trace only, no E2B
            #                            verification (marked "suggested").
            # The repo_dir branch below is kept 100% intact; the else branch is
            # the new remote-website fallback.
            repo_dir = self.job.config.get("repo_dir") or self.job.repo_url
            if repo_dir and os.path.exists(repo_dir):
                logger.info(f"Running source-aware analysis on repo directory: {repo_dir}")
                analyzer = StaticAnalyzer(repo_dir=repo_dir)
                fixer = OpenRouterFixer()

                issues = self.db.query(Issue).filter_by(job_id=self.job.id).all()
                for issue in issues:
                    try:
                        ctx = analyzer.extract_source_context(issue.stack_trace, issue.error_message)
                        if ctx:
                            issue.source_file = os.path.basename(ctx.file_path)
                            issue.source_line = ctx.line_number

                        fix_res = await fixer.generate_fix(issue, ctx)

                        # Discover the matching test file for the patched source file so
                        # E2B can run pytest instead of just a syntax check.
                        test_path: str | None = None
                        if ctx:
                            test_path = _find_test_file(ctx.file_path, repo_dir)

                        verified, _ver_log = VerificationEngine.verify_patch(
                            fix_res.patch,
                            ctx,
                            repo_dir=repo_dir,
                            test_path=test_path,
                        )
                        issue.fix_diff = fix_res.patch
                        issue.fix_confidence = fix_res.confidence
                        issue.fix_status = "verified" if verified else "suggested"
                        issue.description = f"{issue.description}\n\nAI Fix Explanation:\n{fix_res.explanation}"
                        self.db.commit()
                    except Exception as e:
                        logger.error(f"Failed to generate fix for issue {issue.id}: {e}")
            else:
                # Remote-website mode: no local source repo available.
                # Generate AI fix suggestions from the issue title / error
                # message / stack trace alone, then verify them against the
                # LIVE website inside a disposable Daytona sandbox running
                # Playwright (Option 2). When Daytona is unavailable or the
                # live check cannot prove the fix, we fall back to "suggested".
                #
                # The repo-based path above is 100% intact; this branch only
                # runs when no local repo_dir exists (e.g. user pasted a
                # random URL in the website UI).
                reason = "no repo_dir configured" if not repo_dir else f"repo_dir not found: {repo_dir}"
                logger.info(f"Remote-website mode: generating AI fixes without source context ({reason})")
                fixer = OpenRouterFixer()

                # The live URL to replay fixes against. Prefer the issue's
                # first_seen_url when available so the fix is verified on the
                # page where it was actually detected.
                job_url = self.job.url

                issues = self.db.query(Issue).filter_by(job_id=self.job.id).all()
                for issue in issues:
                    try:
                        # No source context available for remote sites.
                        fix_res = await fixer.generate_fix(issue, code_context=None)

                        issue.fix_diff = fix_res.patch
                        issue.fix_confidence = fix_res.confidence

                        # Attempt live-site verification in a Daytona sandbox.
                        # Build a signature from the issue's error message /
                        # stack trace so the runner can detect whether the
                        # original symptom disappears after injecting the patch.
                        live_url = issue.first_seen_url or job_url
                        signature = (
                            (issue.error_message or issue.stack_trace or issue.title or "")
                            .strip()
                            .splitlines()[0][:200]
                        )
                        verified, ver_log = VerificationEngine.verify_live_patch(
                            patch=fix_res.patch,
                            target_url=live_url,
                            issue_category=str(issue.category.value)
                            if hasattr(issue.category, "value")
                            else str(issue.category),
                            issue_signature=signature,
                        )
                        if verified:
                            issue.fix_status = "verified"
                            logger.info(f"Issue {issue.id}: live-site fix VERIFIED in Daytona sandbox")
                        else:
                            issue.fix_status = "suggested"
                            logger.info(f"Issue {issue.id}: live-site verification skipped/failed ({ver_log[:120]})")
                        issue.description = (
                            f"{issue.description}\n\nAI Fix Explanation:\n{fix_res.explanation}"
                            f"\n\nLive Verification Log:\n{ver_log}"
                        )
                        self.db.commit()
                    except Exception as e:
                        logger.error(f"Failed to generate fix for issue {issue.id}: {e}")

        # Step 6: Finalize Job report state
        try:
            issues = self.db.query(Issue).filter_by(job_id=self.job.id).all()

            # DeepSeek-parity: Record form anti-pattern issues.
            # Scans the registry for endpoints flagged by DOMMiner with
            # ``extra["form_antipattern"]`` (GET-method forms handling
            # sensitive data) and creates ADVISORY issues for them.
            try:
                from src.models.database import Issue as IssueModel
                from src.models.database import IssueCategory, IssueSeverity

                existing_fps = {i.fingerprint for i in issues}
                for ep in self.registry.get_all():
                    antipattern = ep.extra.get("form_antipattern") if hasattr(ep, "extra") else None
                    if antipattern:
                        fp = f"form_antipattern::{ep.url}"
                        if fp not in existing_fps:
                            issue = IssueModel(
                                job_id=self.job.id,
                                fingerprint=fp,
                                title=f"FORM_ANTIPATTERN: GET-method form on sensitive path — {ep.url}",
                                description=(
                                    f"{antipattern}. GET-method forms expose submitted "
                                    f"data in the URL/query string, which is visible in "
                                    f"browser history, server logs, and referrer headers. "
                                    f"Sensitive forms (contact/login/register/subscribe) "
                                    f"should use POST."
                                ),
                                severity=IssueSeverity.ADVISORY,
                                category=IssueCategory.NETWORK_FAILURE,  # Closest existing category
                                first_seen_url=ep.url,
                                error_message=antipattern,
                                stack_trace="",
                                repro_steps=[],
                                fix_status="unfixed",
                            )
                            self.db.add(issue)
                            self.db.commit()
                            issues.append(issue)
                            existing_fps.add(fp)
                logger.info(f"Form anti-pattern scan complete ({len(issues)} total issues)")
            except Exception as e:
                logger.warning(f"Form anti-pattern issue recording failed (non-fatal): {e}")

            # Security / common-endpoint probe: after the crawl, test ~130
            # common paths (.env, .git/config, wp-admin, robots.txt, api/health…)
            # and report exposed sensitive files / admin panels / debug endpoints
            # in a separate "Security/Common" category (IssueCategory.SECURITY).
            # Non-fatal — failures never break the main report pipeline.
            try:
                from src.discovery.security_probe import run_security_probe

                existing_fps = {i.fingerprint for i in issues}
                security_issues, probe_results, spa_type = run_security_probe(
                    base_url=self.job.url,
                    job=self.job,
                    db=self.db,
                    existing_fingerprints=existing_fps,
                )
                if security_issues:
                    issues.extend(security_issues)
                    logger.info(
                        f"Security probe added {len(security_issues)} findings (total issues now {len(issues)})"
                    )
                # Persist the full probe verdicts + detected SPA type for the
                # report's probe table / summary stats. Additive job.config
                # keys — no DB schema change; old jobs render without them.
                config = dict(self.job.config or {})
                config["security_probe_results"] = [
                    {
                        "path": r.path,
                        "url": r.url,
                        "http_status": r.status_code,
                        "verdict": r.verdict.value,
                        "category": r.category,
                        "display_severity": r.display_severity,
                        "validation_reason": r.validation_reason,
                        "content_type": r.content_type,
                        "body_size": r.body_size,
                    }
                    for r in probe_results
                ]
                config["spa_type"] = spa_type
                self.job.config = config
            except Exception as e:
                logger.warning(f"Security probe pass failed (non-fatal): {e}")

            # DeepSeek-parity: Aggregate external service health.
            # Groups all CSP/network issues by external domain so the
            # report can show a table like DeepSeek's "external services"
            # section (e.g. "GA4 — partial", "Wisepops — broken",
            # "Self-hosted analytics — broken").
            external_services = self._aggregate_external_services(issues)
            self.job.config = self.job.config or {}
            self.job.config["external_services"] = external_services

            # DeepSeek-parity: Technical analysis layer (CMS detection,
            # robots.txt content analysis, sitemap categorization, form
            # field analysis). Best-effort — failures are non-fatal.
            try:
                from src.analysis.technical_analysis import run_technical_analysis

                technical_findings = run_technical_analysis(self.job.url, self.registry)
                self.job.config["technical_findings"] = technical_findings
                logger.info(
                    f"Technical analysis complete: CMS={technical_findings.get('cms', {}).get('detected', 'Unknown')}, "
                    f"robots.txt={'present' if technical_findings.get('robots', {}).get('present') else 'absent'}, "
                    f"sitemaps={technical_findings.get('sitemaps', {}).get('total', 0)}"
                )
            except Exception as e:
                logger.warning(f"Technical analysis failed (non-fatal): {e}")
                self.job.config["technical_findings"] = {}

            self.db.commit()

            # Optional REAL Lighthouse audit of the seed page (worker context,
            # Node CLI, enabled via LIGHTHOUSE_ENABLED). Non-fatal; merged over
            # the per-page computed scores in the report summary.
            with contextlib.suppress(Exception):
                from src.analysis.lighthouse_scorer import (
                    merge_lighthouse_sections,
                    run_lighthouse_audit,
                )

                real_lh = run_lighthouse_audit(self.job.url)
                if real_lh:
                    seed_ep = self.registry.get(self.job.url, "GET")
                    computed = None
                    if seed_ep is not None and getattr(seed_ep, "performance_data", None):
                        first = next(iter(seed_ep.performance_data.values()), None) or {}
                        computed = first.get("lighthouse")
                    merged = merge_lighthouse_sections(computed, real_lh)
                    config = dict(self.job.config or {})
                    config["lighthouse_seed"] = merged
                    self.job.config = config  # full reassignment for SQLAlchemy tracking
                    self.db.commit()
                    logger.info(
                        "Lighthouse CLI audit of seed page: score=%s (source=%s)",
                        merged.get("score"), merged.get("source"),
                    )

            from src.analysis.report_generator import generate_static_html_report

            _p = getattr(self, "_iso_paths", None) or {}
            # Prefer tenant-isolated report path; generator still writes reports/ by default
            report_path = generate_static_html_report(self.job_id, self.job, self.registry, issues)
            # Mirror report into tenant job folder for isolation (non-destructive copy)
            try:
                import shutil

                tenant_report = _p.get("report_html")
                if tenant_report and report_path and os.path.exists(report_path):
                    os.makedirs(os.path.dirname(tenant_report), exist_ok=True)
                    shutil.copy2(report_path, tenant_report)
            except Exception as copy_err:
                logger.debug("Tenant report mirror skipped: %s", copy_err)
            logger.info(f"Generated static HTML report at {report_path}")

            # Generate Allure-compatible results
            from src.analysis.allure_reporter import AllureReporter

            allure_out = _p.get("allure") or f"reports/allure-results/{self.job_id}"
            allure_reporter = AllureReporter(output_dir=allure_out)
            allure_dir = allure_reporter.generate(
                job=self.job,
                registry=self.registry,
                issues=issues,
                a11y_results=self.a11y_results,
                api_summary=getattr(self, "api_test_summary", None),
            )
            logger.info(f"Generated Allure results at {allure_dir}")

            # WCAG 2.1 conformance + VPAT-style ACR exports (non-fatal).
            # Evidence: axe-core reports collected per visited page.
            with contextlib.suppress(Exception):
                from src.analysis.wcag_export import (
                    build_wcag_conformance,
                    render_vpat_html,
                    render_vpat_markdown,
                )

                a11y_list = list(self.a11y_results.values())
                if a11y_list:
                    conformance = build_wcag_conformance(a11y_list, base_url=self.job.url)
                    vpat_md = render_vpat_markdown(conformance, product_name=self.job.url)
                    vpat_html = render_vpat_html(conformance, product_name=self.job.url)
                    export_dir = (_p.get("job_root") or "reports")
                    os.makedirs(export_dir, exist_ok=True)
                    md_path = f"{export_dir}/vpat_acr_{self.job_id}.md"
                    html_path = f"{export_dir}/vpat_acr_{self.job_id}.html"
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(vpat_md)
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(vpat_html)
                    # also stash JSON for the API/UI
                    import json as _json

                    with open(f"{export_dir}/wcag_conformance_{self.job_id}.json", "w", encoding="utf-8") as f:
                        _json.dump(conformance, f, indent=2)
                    logger.info(f"Generated WCAG/VPAT exports at {export_dir}")
        except Exception as e:
            logger.error(f"Failed to generate reports: {e}")

        # Only mark COMPLETED if the job wasn't cancelled by the user.
        # If cancelled, the status was already set by the stop endpoint
        # and we would have returned early above — this is a safety net.
        if not cancelled:
            self.job.status = JobStatus.COMPLETED
            self.job.completed_at = datetime.now(UTC)
            self.db.commit()

    def _aggregate_external_services(self, issues: list[Any]) -> list[dict[str, Any]]:
        """Group all CSP/network issues by external service domain.

        Mirrors the DeepSeek report's "external services" table that shows
        which third-party integrations are broken vs. working (e.g.
        "Google Analytics — partial", "Wisepops — broken",
        "Self-hosted analytics — broken").

        Non-destructive: only reads issues, does not modify them.
        """
        base_netloc = urlparse(self.job.url).netloc.lower()
        services: dict[str, dict[str, Any]] = {}

        for issue in issues:
            message = issue.error_message or issue.description or ""
            # Extract all domains from the issue message
            domains = re.findall(r"https?://([a-zA-Z0-9.\-]+)", message)
            for domain in domains:
                domain = domain.lower()
                # Skip the site's own domain
                if domain == base_netloc or domain.endswith(f".{base_netloc}"):
                    continue

                if domain not in services:
                    services[domain] = {
                        "domain": domain,
                        "issue_count": 0,
                        "categories": set(),
                        "severities": set(),
                        "status": "unknown",
                        "sample_issue": "",
                    }
                services[domain]["issue_count"] += 1
                cat = str(issue.category.value) if hasattr(issue.category, "value") else str(issue.category)
                sev = str(issue.severity.value) if hasattr(issue.severity, "value") else str(issue.severity)
                services[domain]["categories"].add(cat)
                services[domain]["severities"].add(sev)
                if not services[domain]["sample_issue"]:
                    services[domain]["sample_issue"] = (issue.title or "")[:200]

        # Determine status per service
        for svc in services.values():
            cats = svc["categories"]
            sevs = svc["severities"]
            if "blocking" in sevs or "BLOCKING" in str(sevs):
                svc["status"] = "broken"
            elif "CSP_VIOLATION" in cats or "csp_violation" in str(cats).lower():
                svc["status"] = "blocked-by-csp"
            elif "NETWORK_FAILURE" in cats or "network_failure" in str(cats).lower():
                svc["status"] = "failing"
            else:
                svc["status"] = "issues"
            # Convert sets to sorted lists for JSON serialization
            svc["categories"] = sorted(svc["categories"])
            svc["severities"] = sorted(svc["severities"])

        return list(services.values())

    async def crawl_url_on_browser(
        self, url: str, method: str, depth: int, browser_engine: str, page: InstrumentedPage, parent_node: FrontierNode
    ) -> tuple[bool, str]:
        """Execute loading a URL inside a fanned-out browser context."""
        try:
            # Replay path actions if node is linked to an SPA state transition chain (Step O)
            if parent_node.metadata.get("spa_transitions"):
                await self.replay_spa_transitions(page, parent_node.metadata["spa_transitions"])

            # Pace the testing speed (throttle delay to simulate human timing)
            await asyncio.sleep(getattr(self, "crawl_delay", 1.5))

            # Load page — use domcontentloaded + 45s timeout to prevent hangs
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as goto_err:
                logger.warning(f"Initial goto failed for {url}: {goto_err}. Retrying with load state...")
                # Retry once with 'load' wait state and longer timeout
                try:
                    response = await page.goto(url, wait_until="load", timeout=60000)
                except Exception as goto_err2:
                    logger.error(f"Goto retry also failed for {url}: {goto_err2}")
                    raise

            # Handle Retry-After / 429 / 503 backoff
            if response and response.status in (429, 503):
                retry_after_str = response.headers.get("retry-after")
                sleep_time = 5.0
                if retry_after_str:
                    with contextlib.suppress(ValueError):
                        sleep_time = float(retry_after_str)
                logger.warning(f"Received HTTP {response.status} rate limit. Backing off for {sleep_time}s...")
                await asyncio.sleep(sleep_time)
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as retry_err:
                    logger.error(f"Retry goto after rate-limit backoff failed for {url}: {retry_err}")
                    raise

            # Record the real HTTP status on the endpoint so downstream
            # visited/failed marking and health scoring reflect reality
            # instead of a hardcoded 200.
            if response is not None:
                ep = self.registry.get(url, method)
                if ep is not None:
                    ep.last_status_code = response.status

            # Content-Type Gating: prevent crawling raw JS code bundles or JSON endpoints
            if response:
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "text/html" not in content_type:
                    logger.info(f"Skipping non-HTML page navigation (Content-Type: {content_type}) for {url}")
                    state_record = CrawlState(
                        job_id=self.job.id,
                        url=url,
                        method=method,
                        depth=depth,
                        visit_count=1,
                        status="skipped",
                        last_status_code=response.status,
                        priority_score=parent_node.priority_score,
                        discovered_via=parent_node.discovered_via,
                    )
                    self.db.add(state_record)
                    self.db.commit()
                    return True, "Skipped non-HTML content"

            # Register the VISITED page itself (document navigation is not
            # captured by the request-interception listener, which only sees
            # subresources). Without this, perf data / screenshots / visual
            # diffs were silently dropped for every crawled page.
            try:
                _, _was_new = self.registry.add(
                    url=url,
                    method=method,
                    source=EndpointSource.USER_PROVIDED,
                    discovered_on_page=parent_node.parent_url or "",
                )
            except Exception as reg_err:
                logger.debug("visited-page registration skipped for %s: %s", url, reg_err)

            # Fetch content and compute structure early to check for pagination traps
            html_content = await page.page.content()
            template_id, similarity = self.dom_similarity.get_template_id(html_content)
            self.dom_similarity.record_visit(template_id)

            if self.dom_similarity.should_gate_links(template_id):
                logger.warning(
                    f"Structural pagination trap detected! Template '{template_id}' has been seen "
                    f"more than {self.dom_similarity.visit_limit} times. "
                    f"Aborting heavy analysis and link extraction on {url} (similarity: {similarity:.2f})."
                )
                state_record = CrawlState(
                    job_id=self.job.id,
                    url=url,
                    method=method,
                    depth=depth,
                    visit_count=1,
                    status="skipped",
                    last_status_code=response.status if response else 200,
                    priority_score=parent_node.priority_score,
                    discovered_via=parent_node.discovered_via,
                )
                self.db.add(state_record)
                self.db.commit()
                return True, f"Aborted: Template '{template_id}' limit reached"

            await page.page.wait_for_timeout(2000)  # Wait for initial SPA rendering

            # Scroll down systematically in 5 steps to trigger lazy loading and reveal footer links
            try:
                for i in range(1, 6):
                    await page.page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {i / 5})")
                    await page.page.wait_for_timeout(400)
            except Exception as e:
                logger.warning(f"Error executing scroll step: {e}")

            # Re-fetch content (post-scrolling) to capture any lazy-loaded DOM elements
            html_content = await page.page.content()

            # DeepSeek-parity: Extract page title for the report's page categorization.
            # Stored on the endpoint's extra dict (non-destructive).
            try:
                page_title = await page.page.title()
                if page_title:
                    ep = self.registry.get(url, method)
                    if ep is not None:
                        ep.extra["page_title"] = page_title
            except Exception as e:
                logger.debug(f"Could not extract page title for {url}: {e}")

            # Run assertions/oracles (Step R)
            report = page.console.finalize()

            has_actionable_errors = any(entry.severity.value in ("error", "critical") for entry in report.entries)
            repro_attempts = []
            if has_actionable_errors:
                logger.info(f"Actionable issues detected on {url}. Running reproducibility retries...")
                for attempt in range(1, 3):
                    from src.core.console_capture import ConsoleCapture

                    temp_capture = ConsoleCapture(url=url, browser=browser_engine)
                    # Swap capture object temporarily
                    old_capture = page.console
                    page.console = temp_capture
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        await page.page.wait_for_timeout(1000)
                        temp_report = page.console.finalize()
                        temp_has_errors = any(e.severity.value in ("error", "critical") for e in temp_report.entries)
                        repro_attempts.append({"attempt": attempt, "occurred": temp_has_errors})
                    except Exception as e:
                        repro_attempts.append({"attempt": attempt, "occurred": False, "error": str(e)})
                    finally:
                        page.console = old_capture

            self.triage_console_report(report, url, browser_engine, repro_attempts)

            # DeepSeek report Step 1: SPA client-side router 404 detection.
            # The HTTP response may be 200 (the SPA shell loaded fine) but the
            # client router could not match the requested path — an effective
            # 404 that the HTTP status code hides. We scan the captured console
            # entries for router "no match" messages and, if found, mark the
            # endpoint's *effective* status as a 404 in its `extra` dict so the
            # report can display "HTTP 200 → Effective 404 SPA Error".
            try:
                from src.core.console_capture import ConsoleCategory, detect_spa_route_error

                spa_framework: str | None = None
                for entry in report.entries:
                    if entry.category == ConsoleCategory.SPA_ROUTE_ERROR:
                        spa_framework = detect_spa_route_error(entry.message) or "generic"
                        break
                if spa_framework:
                    ep = self.registry.get(url, method)
                    if ep is not None:
                        ep.extra["effective_status_code"] = "404"
                        ep.extra["effective_status_reason"] = f"SPA route error ({spa_framework})"
                        ep.extra["effective_status_detail"] = (
                            f"Client-side router ({spa_framework}) could not match "
                            f"the requested path. HTTP shell returned 200 but the "
                            f"page is effectively a 404."
                        )
                        logger.info(
                            f"SPA route 404 detected on {url} (framework={spa_framework}). "
                            f"Marking effective status as 404."
                        )
            except Exception as spa_err:
                logger.debug(f"SPA route detection failed for {url}: {spa_err}")

            # Run accessibility audit (axe-core injection)
            try:
                a11y_report = await self.a11y_auditor.audit(page, url=url)
                self.a11y_results[url] = a11y_report
                # Store on the endpoint record
                ep = self.registry.get(url, method)
                if ep is not None:
                    if not hasattr(ep, "a11y_report"):
                        ep.a11y_report = None
                    ep.a11y_report = a11y_report
            except Exception as e:
                logger.warning(f"A11y audit failed for {url}: {e}")

            # Deep mining inputs and comments (Step H)
            mine_html_dom(
                html_content=html_content,
                page_url=url,
                base_url=self.job.url,
                registry=self.registry,
                scope=self.job.scope,
            )

            # Introspect API assets (Step I)
            if url.endswith(".js"):
                await process_js_endpoint_mining(url, self.registry, self.job.url)
            elif "/graphql" in url:
                await execute_graphql_introspection(url, self.registry)

            # Take snapshot for the final report (tenant-isolated when available)
            import os

            _p = getattr(self, "_iso_paths", None) or {}
            snapshot_dir = _p.get("snapshots") or f"reports/snapshots/{self.job_id}"
            os.makedirs(snapshot_dir, exist_ok=True)
            safe_url = url.replace("://", "_").replace("/", "_").replace("?", "_").replace("=", "_")
            # Limit filename length
            if len(safe_url) > 100:
                safe_url = safe_url[-100:]
            snapshot_path = f"{snapshot_dir}/{safe_url}_{browser_engine}.png"
            # DeepSeek-parity / size optimization: also generate a small JPEG
            # thumbnail for embedding in the HTML report. The full PNG stays
            # on disk and is linked via relative path, so the report HTML
            # shrinks from ~250MB to a few MB without losing any screenshots.
            thumb_path = f"{snapshot_dir}/{safe_url}_{browser_engine}_thumb.jpg"
            try:
                await page.page.screenshot(path=snapshot_path, full_page=True)
                # Generate a compressed JPEG thumbnail (max 400px wide, q=70)
                try:
                    from PIL import Image

                    img = Image.open(snapshot_path)
                    max_w = 400
                    if img.width > max_w:
                        ratio = max_w / img.width
                        img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
                    img = img.convert("RGB")
                    img.save(thumb_path, "JPEG", quality=70, optimize=True)
                except Exception as te:
                    logger.debug(f"Thumbnail generation failed for {url}: {te}")
                    thumb_path = ""  # fall back to full image
                # Store snapshot reference in registry
                ep = self.registry.get(url, method)
                if ep is not None:
                    if not hasattr(ep, "snapshots"):
                        ep.snapshots = {}
                    ep.snapshots[browser_engine] = snapshot_path
                    if not hasattr(ep, "thumbnails"):
                        ep.thumbnails = {}
                    ep.thumbnails[browser_engine] = thumb_path if thumb_path and os.path.exists(thumb_path) else ""
            except Exception as e:
                logger.warning(f"Failed to capture snapshot for {url}: {e}")

            # Visual regression comparison
            if os.path.exists(snapshot_path):
                try:
                    vr_result = self.visual_engine.compare(url, snapshot_path)
                    ep = self.registry.get(url, method)
                    if ep is not None:
                        if not hasattr(ep, "visual_diff"):
                            ep.visual_diff = None
                        ep.visual_diff = vr_result
                except Exception as e:
                    logger.warning(f"Visual regression comparison failed for {url}: {e}")

            # Collect CDP performance metrics and Navigation Timing data
            cdp_metrics = await page.get_cdp_performance_metrics()
            nav_timing = await page.get_navigation_timing()
            if cdp_metrics or nav_timing:
                perf_data = {
                    "cdp_metrics": cdp_metrics,
                    "nav_timing": nav_timing,
                }
                # Lighthouse-style scoring (FCP/LCP/TBT/SI/CLS) from live data.
                # Non-fatal enrichment — never breaks the crawl.
                try:
                    from src.analysis.lighthouse_scorer import (
                        collect_paint_and_shift,
                        compute_lighthouse_style_scores,
                    )

                    paint_and_shift = await collect_paint_and_shift(page.page)
                    resource_data = {
                        "long_tasks": paint_and_shift.get("long_tasks") or [],
                        "layout_shift": paint_and_shift.get("layout_shift"),
                    }
                    lh_section = compute_lighthouse_style_scores(
                        nav_timing, cdp_metrics, paint_and_shift, resource_data
                    )
                    perf_data["lighthouse"] = lh_section
                except Exception as _lh_err:
                    logger.warning("Lighthouse scoring skipped for %s: %s", url, _lh_err)
                    perf_data["lighthouse"] = lh_section
                # Store performance data on the endpoint record
                ep = self.registry.get(url, method)
                if ep is not None:
                    if not hasattr(ep, "performance_data"):
                        ep.performance_data = {}
                    ep.performance_data[browser_engine] = perf_data
                logger.info(
                    f"Performance metrics collected for {url}: "
                    f"JSHeapUsed={cdp_metrics.get('JSHeapUsedSize', 'N/A')}, "
                    f"TTFB={nav_timing.get('responseStart', 'N/A')}"
                )

            # Handle SPA Transitions / state changes (Step K)
            # Evaluate interactive buttons on live SPA
            # Disabled to prevent destructive random clicking:
            # if depth < self.max_depth:
            #     await self.explore_spa_state_changes(page, url, depth)

            # Queue newly discovered unvisited endpoints to frontier
            unvisited = self.registry.get_unvisited()
            for ep in unvisited:
                # 1. Skip static resource assets and WebSocket endpoints to avoid bloating queue
                if ep.endpoint_type in (EndpointType.STATIC_ASSET, EndpointType.WEBSOCKET):
                    ep.status = EndpointStatus.SKIPPED
                    continue

                # 2. Skip raw network-captured XHR/API failures and non-page debug/console endpoints
                # from being loaded as Playwright browser page targets (they remain in EndpointRegistry for reports)
                is_frontend_page_link = (
                    ep.source
                    in (
                        EndpointSource.DOM_LINK,
                        EndpointSource.SITEMAP_XML,
                        EndpointSource.USER_PROVIDED,
                        EndpointSource.REDIRECT,
                    )
                    or ep.endpoint_type == EndpointType.PAGE
                )
                if not is_frontend_page_link and (
                    ep.source == EndpointSource.NETWORK_CAPTURE
                    or ep.endpoint_type in (EndpointType.DEBUG, EndpointType.SPEC)
                ):
                    ep.status = EndpointStatus.SKIPPED
                    continue

                priority = calculate_priority_score(
                    url=ep.url,
                    method=ep.method,
                    depth=depth + 1,
                    endpoint_type=ep.endpoint_type.value,
                    source=ep.source.value,
                )

                new_node = FrontierNode(
                    url=ep.url,
                    method=ep.method,
                    depth=depth + 1,
                    priority_score=priority,
                    parent_url=url,
                    discovered_via=ep.source.value,
                    endpoint_type=ep.endpoint_type.value,
                )
                self.frontier.push(new_node)
                ep.status = EndpointStatus.QUEUED

            # Save CrawlState record in Postgres
            state_record = CrawlState(
                job_id=self.job.id,
                url=url,
                method=method,
                depth=depth,
                visit_count=1,
                status="visited",
                last_status_code=response.status if response else None,
                priority_score=parent_node.priority_score,
                discovered_via=parent_node.discovered_via,
            )
            self.db.add(state_record)
            self.db.commit()

            return True, "Success"

        except Exception as e:
            logger.error(f"Error crawling {url} on {browser_engine}: {e}")
            return False, str(e)

    async def replay_spa_transitions(self, page: InstrumentedPage, transitions: list[dict[str, Any]]) -> None:
        """Replay a chain of user actions to reach a specific SPA DOM state (Step O)."""
        for action in transitions:
            selector = action.get("selector")
            action_type = action.get("action_type")
            value = action.get("value")

            try:
                loc = page.page.locator(selector).first
                if await loc.count() > 0:
                    if action_type == "click":
                        await loc.click()
                    elif action_type == "fill" and value:
                        await loc.fill(value)
                    await page.page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Failed replaying SPA step {action_type} on {selector}: {e}")

    async def explore_spa_state_changes(self, page: InstrumentedPage, page_url: str, depth: int) -> None:
        """Check for state changes in a live SPA view when clicking elements."""
        # Get start DOM signature hash
        start_hash = await page.page.evaluate(LIVE_DOM_HASH_JS)

        # Add initial State to graph
        self.spa_graph.add_state(SPAState(state_hash=start_hash, url=page_url, is_visited=True))

        # Locate interactive elements (buttons and links)
        buttons = page.page.locator("button, a.btn, .clickable")
        btn_count = await buttons.count()

        # Limit elements tested per page to avoid infinite loops/bloat
        test_limit = min(btn_count, 15)
        for i in range(test_limit):
            try:
                # Reload/restore to start state before checking next button
                await page.page.goto(page_url)
                await page.page.wait_for_timeout(1000)

                btn = buttons.nth(i)
                if not await btn.is_visible():
                    continue

                selector = f"nth-child({i})"  # Symbolic selector representation

                # Check for destructive elements gating (Step P)
                btn_text = await btn.inner_text()
                if is_destructive_action(page_url, "POST", selector, btn_text) and not self.allow_destructive:
                    logger.warning(f"Gating destructive action click: '{btn_text}'")
                    continue

                # Click element
                await btn.click()
                await page.page.wait_for_timeout(1000)

                # Check if URL changed
                new_url = page.page.url
                if new_url != page_url:
                    # Treat as page navigation, standard link discovery will handle it
                    continue

                # Evaluate new DOM structure
                new_hash = await page.page.evaluate(LIVE_DOM_HASH_JS)
                if new_hash != start_hash:
                    # Found a hidden/new SPA view state!
                    new_state = SPAState(state_hash=new_hash, url=page_url)
                    self.spa_graph.add_state(new_state)

                    # Add edge transition mapping
                    transition = SPATransition(
                        source_hash=start_hash,
                        target_hash=new_hash,
                        action_type="click",
                        selector=f"button >> text={btn_text}" if btn_text else f"nth={i}",
                        url=page_url,
                    )
                    self.spa_graph.add_transition(transition)

                    # Queue a virtual frontier node to explore this new DOM state later
                    # Store action transitions required to reach it
                    actions_path = self.spa_graph.find_path(start_hash, new_hash)

                    virtual_url = f"{page_url}#state={new_hash}"

                    _, is_new = self.registry.add(
                        url=virtual_url, method="GET", source=EndpointSource.DOM_LINK, discovered_on_page=page_url
                    )

                    if is_new:
                        priority = calculate_priority_score(virtual_url, "GET", depth + 1)
                        new_node = FrontierNode(
                            url=virtual_url,
                            method="GET",
                            depth=depth + 1,
                            priority_score=priority,
                            metadata={
                                "spa_transitions": [
                                    {"selector": t.selector, "action_type": t.action_type, "value": t.value}
                                    for t in (actions_path or [])
                                ]
                            },
                        )
                        self.frontier.push(new_node)
            except Exception:
                continue

    def triage_console_report(
        self, report: Any, url: str, browser: str, repro_attempts: list[dict[str, Any]] | None = None
    ) -> None:
        """Triage captured console/error entries and store normalized issues (Steps R & S).

        Performance notes:
        - The job's pre-existing DB issues are loaded once and cached on the
          instance (worker-restart recovery) instead of re-queried per entry.
        - Fuzzy root-cause matching is gated behind difflib's cheap upper-bound
          ratios (real_quick_ratio/quick_ratio), so the expensive full ratio()
          only runs for plausible candidates. The prefilters are upper bounds
          on ratio(), so no true match is ever skipped.
        - All DB writes in the batch are flushed and committed ONCE at the end
          instead of a commit round-trip per console entry.
        """
        import difflib

        # Lazily cache this job's existing issues (once per engine instance)
        if getattr(self, "_db_issues_cache", None) is None:
            self._db_issues_cache = self.db.query(Issue).filter_by(job_id=self.job.id).all()

        spa_config: dict[str, Any] | None = None

        def _fuzzy_match(message: str, candidates: Any) -> Any:
            """Return the first candidate whose error_message fuzzy-matches (>0.85)."""
            for candidate in candidates:
                sm = difflib.SequenceMatcher(None, message, candidate.error_message)
                # real_quick_ratio >= quick_ratio >= ratio — cheap rejectors
                if sm.real_quick_ratio() > 0.85 and sm.quick_ratio() > 0.85 and sm.ratio() > 0.85:
                    return candidate
            return None

        # report contains a list of console entries from ConsoleCapture finalizer
        for entry in report.entries:
            # We only record warning/error/critical events as issues
            if entry.severity.value not in ("error", "critical", "warning"):
                continue

            # Noise gate: Angular dumps an empty "STACKTRACE:" fragment as a
            # separate console error alongside its client-router errors —
            # it carries zero diagnostic content. Skip bare/empty messages
            # (real stack traces have content after the prefix and pass).
            stripped = (entry.message or "").strip()
            if not stripped or stripped.rstrip(":").strip().upper() == "STACKTRACE":
                continue

            # SPA route errors ("Cannot match any routes" etc.) are NOT
            # functional/http issues — the HTTP shell loaded fine (200);
            # the client-side router just didn't match the path. Record
            # them to job.config["spa_route_404s"] for the report instead
            # of creating Issue rows (SPA false-positive elimination).
            if entry.category == ConsoleCategory.SPA_ROUTE_ERROR:
                if spa_config is None:
                    spa_config = dict(self.job.config or {})
                spa_404s = spa_config.get("spa_route_404s") or []
                if not any(e.get("url") == url and e.get("message") == entry.message for e in spa_404s):
                    spa_404s.append(
                        {
                            "url": url,
                            "message": entry.message,
                            "browser": browser,
                        }
                    )
                    spa_config["spa_route_404s"] = spa_404s
                continue

            # Deduplicate by fingerprint
            fingerprint = entry.fingerprint()

            # Root-cause clustering: exact fingerprint hit first (O(1)),
            # then fuzzy duplicate check against in-memory issues
            matched_issue = self.issues_dedup.get(fingerprint)
            if matched_issue is None:
                matched_issue = _fuzzy_match(entry.message, self.issues_dedup.values())
                if matched_issue is not None:
                    fingerprint = matched_issue.fingerprint

            if matched_issue is None:
                # Check cached DB issues for this job (in case of worker restarts)
                db_issue = next((i for i in self._db_issues_cache if i.fingerprint == fingerprint), None)

                if db_issue is None:
                    db_issue = _fuzzy_match(entry.message, self._db_issues_cache)
                    if db_issue is not None:
                        fingerprint = db_issue.fingerprint

                if db_issue:
                    self.issues_dedup[fingerprint] = db_issue
                else:
                    # Map console error category back to IssueCategory
                    category_map = {
                        ConsoleCategory.JS_ERROR: IssueCategory.JS_ERROR,
                        ConsoleCategory.UNHANDLED_REJECTION: IssueCategory.JS_ERROR,
                        ConsoleCategory.CORS_ERROR: IssueCategory.CORS_ERROR,
                        ConsoleCategory.CSP_VIOLATION: IssueCategory.CSP_VIOLATION,
                        ConsoleCategory.MIXED_CONTENT: IssueCategory.MIXED_CONTENT,
                        ConsoleCategory.NETWORK_FAILURE: IssueCategory.NETWORK_FAILURE,
                        ConsoleCategory.RESOURCE_LOAD_FAILURE: IssueCategory.RESOURCE_LOAD_FAILURE,
                        ConsoleCategory.FRAMEWORK_ERROR: IssueCategory.FRAMEWORK_ERROR,
                        ConsoleCategory.SECURITY: IssueCategory.SECURITY,
                        ConsoleCategory.GA_POST_FAILURE: IssueCategory.NETWORK_FAILURE,
                        ConsoleCategory.SPA_ROUTE_ERROR: IssueCategory.HTTP_ERROR,
                    }
                    category = category_map.get(entry.category, IssueCategory.CONSOLE_ERROR)

                    severity = IssueSeverity.FUNCTIONAL
                    if entry.severity.value == "critical":
                        severity = IssueSeverity.BLOCKING
                    elif entry.severity.value == "warning":
                        severity = IssueSeverity.ADVISORY

                    # Issue Reproducibility Confirmation: check attempts
                    is_reproducible = (
                        any(att.get("occurred") for att in (repro_attempts or [])) if repro_attempts else True
                    )

                    # Create a new triaged Issue (Step S)
                    db_issue = Issue(
                        job_id=self.job.id,
                        fingerprint=fingerprint,
                        title=f"{'[FLAKY] ' if not is_reproducible else ''}{category.value.upper()}: {entry.message[:100]}...",
                        description=entry.message,
                        severity=severity,
                        category=category,
                        first_seen_url=url,
                        error_message=entry.message,
                        stack_trace=entry.source_location,
                        repro_steps=repro_attempts or [],
                        fix_status="flaky" if not is_reproducible else "unfixed",
                    )
                    self.db.add(db_issue)
                    self.db.flush()  # assign PK now; commit once at batch end
                    self.issues_dedup[fingerprint] = db_issue
                    self._db_issues_cache.append(db_issue)

                    # Job aggregates count UNIQUE issues, not occurrences
                    self.job.issues_found += 1
                    if severity == IssueSeverity.BLOCKING:
                        self.job.console_errors_found += 1

            # Record occurrences
            issue = self.issues_dedup[fingerprint]

            # Create occurrence
            occ = IssueOccurrence(
                issue_id=issue.id,
                url=url,
                browser=browser,
                error_message=entry.message,
                console_output={"message": entry.message, "location": entry.source_location},
            )
            self.db.add(occ)

            # Increment aggregates
            issue.occurrence_count += 1
            if browser == "chromium":
                issue.browsers_affected["chromium"] = True
            elif browser == "firefox":
                issue.browsers_affected["firefox"] = True
            elif browser == "webkit":
                issue.browsers_affected["webkit"] = True

            issue.browser_count = sum(1 for v in issue.browsers_affected.values() if v)

        # Persist SPA route 404s (batched) + one commit for the whole batch
        if spa_config is not None:
            self.job.config = spa_config
        self.db.commit()
