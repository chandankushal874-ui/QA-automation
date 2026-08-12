import asyncio
import logging
import os
import sys
import uuid

# Ensure we can import from src and tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tests.test_frontier import MockRedis

from src.crawler.engine import CrawlEngine
from src.models.database import Base, Job, JobStatus

# Setup logging to see everything
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

async def run_deep_test():
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "honest_deep_test.db"))
    print(f"Using DB at {db_path}")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db_session = Session()

    job_id = uuid.uuid4()
    target_url = "http://books.toscrape.com"

    # Configure the job for deep testing
    job = Job(
        id=job_id,
        url=target_url,
        scope="same-domain",
        status=JobStatus.PENDING,
        # Let's run primarily on Chromium and Firefox for speed/reliability in tests
        browser_matrix={"chromium": True, "firefox": True, "webkit": False},
        max_pages=50,  # Cap at 50 pages to ensure it finishes within a reasonable time
        max_depth=3,
        max_time_seconds=600,  # 10 minutes max
        config={"repo_dir": "."}
    )
    db_session.add(job)
    db_session.commit()

    print(f"Created Job {job_id} for {target_url}")

    # Use MockRedis as Docker/Redis might not be running natively on Windows
    mock_redis = MockRedis()

    crawler = CrawlEngine(
        db_session=db_session,
        redis_client=mock_redis,
        job_id=str(job_id),
        allow_destructive=False
    )

    try:
        print("Starting deep crawl...")
        await crawler.execute()
        print("Crawl finished successfully.")
    except Exception as e:
        print(f"Crawl failed: {e}")
    finally:
        db_session.close()

if __name__ == "__main__":
    # Workaround for Windows async loop if needed
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run_deep_test())
