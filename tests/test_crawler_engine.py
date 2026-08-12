"""Integration and unit tests for the core CrawlEngine."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.endpoint_registry import EndpointSource
from src.crawler.engine import CrawlEngine, is_destructive_action
from src.models.database import Base, Issue, IssueOccurrence, Job, JobStatus
from tests.test_frontier import MockRedis

# SQLite in-memory DB configuration for CrawlEngine tests
SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    engine = create_engine(SQLITE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_is_destructive_action():
    # 1. DELETE method
    assert is_destructive_action("http://example.com/api/users", "DELETE") is True

    # 2. Destructive paths
    assert is_destructive_action("http://example.com/api/delete-user", "POST") is True
    assert is_destructive_action("http://example.com/reset-database", "GET") is True
    assert is_destructive_action("http://example.com/normal-path", "GET") is False

    # 3. Destructive selectors
    assert is_destructive_action("http://example.com", "POST", selector="button.delete-account-btn") is True
    assert is_destructive_action("http://example.com", "POST", selector="div#destroy-all") is True
    assert is_destructive_action("http://example.com", "POST", selector="button.submit") is False

    # 4. Element text
    assert is_destructive_action("http://example.com", "POST", element_text="Delete Account") is True
    assert is_destructive_action("http://example.com", "POST", element_text="Format Database") is True
    # Safe bypass text
    assert is_destructive_action("http://example.com", "POST", element_text="Remove Filter") is False


@pytest.mark.asyncio
@pytest.mark.browser
async def test_crawler_engine_run(fixture_app, db_session):
    """Test running a full crawl job on the running fixture app.

    Verifies seed discovery, browser context runs, and console error Triaging.
    """
    # 1. Create a Job in database
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        url=fixture_app,
        scope="same-domain",
        status=JobStatus.PENDING,
        browser_matrix={"chromium": True, "firefox": False, "webkit": False},
        max_pages=8,  # small limit for testing; must cover the high-priority
        # JSON endpoints (/admin-backup/, /api/auth/*) that tie with
        # /console-errors at depth 1 and sort before it lexicographically,
        # otherwise the budget is exhausted one pop before the seeded page
        max_depth=2,
        max_time_seconds=60,
        config={"repo_dir": "."},  # Index current workspace for source analysis
    )
    db_session.add(job)
    db_session.commit()

    # 2. Initialize MockRedis
    mock_redis = MockRedis()

    # 3. Initialize and execute CrawlEngine
    engine = CrawlEngine(
        db_session=db_session,
        redis_client=mock_redis,
        job_id=str(job_id),
        allow_destructive=False,  # Gate dangerous paths
    )

    # Let's seed the registry with the console-errors page so we definitely hit it
    engine.registry.add(url=f"{fixture_app}/console-errors", method="GET", source=EndpointSource.USER_PROVIDED)

    # Execute crawler loop
    await engine.execute()

    # 4. Assertions on results in database
    db_session.expire_all()
    updated_job = db_session.query(Job).filter_by(id=job_id).first()

    assert updated_job.status == JobStatus.COMPLETED
    assert updated_job.pages_visited >= 1
    assert updated_job.pages_discovered >= 2

    # Verify that console errors page generated normalized issues in DB
    issues = db_session.query(Issue).filter_by(job_id=job_id).all()
    assert len(issues) > 0

    print("\n--- DETECTED ISSUES ---")
    for issue in issues:
        print(f"[{issue.category}] Severity: {issue.severity} - Message: {issue.error_message}")
        print(f"  -> Suggested Fix status: {issue.fix_status}")
        print(f"  -> Suggested Fix Diff: {issue.fix_diff[:100] if issue.fix_diff else 'None'}")
    print("-----------------------\n")

    # Confirm that we captured network errors or JS errors
    has_relevant_error = any(
        issue.category.value in ("js_error", "network_failure", "http_error", "resource_load_failure")
        for issue in issues
    )
    assert has_relevant_error is True

    # Validate that AI fix engine triggered and populated fields successfully
    assert any(iss.fix_status in ("verified", "suggested") and iss.fix_diff is not None for iss in issues)

    # Check occurrence relationship
    occurrences = db_session.query(IssueOccurrence).join(Issue).filter(Issue.job_id == job_id).all()
    assert len(occurrences) > 0
    assert occurrences[0].browser == "chromium"
