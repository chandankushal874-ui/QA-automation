"""Tests for database models and schema."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.database import (
    AuthProfile,
    Base,
    CrawlState,
    CrawlStateStatus,
    Issue,
    IssueCategory,
    IssueOccurrence,
    IssueSeverity,
    Job,
    JobStatus,
    Journey,
    JourneyStatus,
)

# Use SQLite in-memory database for testing database models
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


def test_job_model_creation(db_session):
    job = Job(
        url="https://example.com",
        scope="same-domain",
        status=JobStatus.PENDING,
        browser_matrix={"chromium": True, "firefox": False, "webkit": False},
        max_pages=500,
        max_depth=5,
        max_time_seconds=1800,
    )
    db_session.add(job)
    db_session.commit()

    assert job.id is not None
    assert isinstance(job.id, uuid.UUID)
    assert job.url == "https://example.com"
    assert job.status == JobStatus.PENDING
    assert job.browser_matrix["chromium"] is True
    assert job.browser_matrix["firefox"] is False
    assert job.max_pages == 500
    assert job.created_at is not None


def test_crawl_state_relationship(db_session):
    job = Job(url="https://example.com")
    db_session.add(job)
    db_session.commit()

    state = CrawlState(
        job_id=job.id,
        url="https://example.com/about",
        url_template="/about",
        method="GET",
        depth=1,
        status=CrawlStateStatus.DISCOVERED,
        priority_score=0.8,
    )
    db_session.add(state)
    db_session.commit()

    # Query back
    queried_job = db_session.query(Job).filter_by(id=job.id).first()
    assert len(queried_job.crawl_states) == 1
    assert queried_job.crawl_states[0].url == "https://example.com/about"
    assert queried_job.crawl_states[0].job.url == "https://example.com"


def test_issue_and_occurrences(db_session):
    job = Job(url="https://example.com")
    db_session.add(job)
    db_session.commit()

    issue = Issue(
        job_id=job.id,
        fingerprint="err_123",
        title="Uncaught TypeError",
        severity=IssueSeverity.BLOCKING,
        category=IssueCategory.JS_ERROR,
        first_seen_url="https://example.com/about",
    )
    db_session.add(issue)
    db_session.commit()

    occ = IssueOccurrence(
        issue_id=issue.id,
        url="https://example.com/about",
        browser="chromium",
        status_code=200,
        error_message="Cannot read properties of null",
    )
    db_session.add(occ)
    db_session.commit()

    # Check relation
    queried_issue = db_session.query(Issue).filter_by(id=issue.id).first()
    assert len(queried_issue.occurrences) == 1
    assert queried_issue.occurrences[0].browser == "chromium"
    assert queried_issue.occurrences[0].issue.title == "Uncaught TypeError"


def test_journey_model(db_session):
    job = Job(url="https://example.com")
    db_session.add(job)
    db_session.commit()

    journey = Journey(
        job_id=job.id,
        name="Login Flow",
        status=JourneyStatus.PLANNED,
        is_critical_path=True,
        steps=[
            {"action": "navigate", "url": "https://example.com/login"},
            {"action": "fill", "selector": "#username", "value": "user"},
            {"action": "click", "selector": "#submit"},
        ],
    )
    db_session.add(journey)
    db_session.commit()

    queried_journey = db_session.query(Journey).filter_by(id=journey.id).first()
    assert queried_journey.is_critical_path is True
    assert len(queried_journey.steps) == 3
    assert queried_journey.steps[0]["action"] == "navigate"


def test_auth_profile(db_session):
    job = Job(url="https://example.com")
    db_session.add(job)
    db_session.commit()

    profile = AuthProfile(
        job_id=job.id,
        login_url="https://example.com/login",
        username="admin",
        password_encrypted="supersecret",
        auth_method="form_login",
        is_authenticated=True,
        cookies=[{"name": "session", "value": "xyz"}],
    )
    db_session.add(profile)
    db_session.commit()

    queried_job = db_session.query(Job).filter_by(id=job.id).first()
    assert queried_job.auth_profile is not None
    assert queried_job.auth_profile.username == "admin"
    assert queried_job.auth_profile.cookies[0]["name"] == "session"
