"""
RQ Worker entry point for processing distributed crawl jobs.

Jobs are pulled from the shared ``default`` queue. Per-user isolation is
enforced inside ``run_background_crawl_job`` / ``CrawlEngine`` via job.owner_id:
  - Redis logical DB + frontier key prefixes
  - Tenant disk paths for reports / baselines / HAR / video
  - API ownership checks (User A cannot open User B jobs)

So concurrent User A and User B crawls never share frontier state or artifacts.
"""

from __future__ import annotations

import logging
import os
import sys

import redis
from rq import Queue, Worker

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    """Launch the RQ worker process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    redis_url = settings.redis_url
    logger.info("Connecting to Redis at %s...", redis_url)

    try:
        redis_conn = redis.Redis.from_url(redis_url)
        redis_conn.ping()
    except Exception as e:
        logger.error("Failed to connect to Redis: %s", e)
        sys.exit(1)

    listen = ["default"]
    env_queues = (os.environ.get("WORKER_QUEUES") or "").strip()
    if env_queues:
        listen = [q.strip() for q in env_queues.split(",") if q.strip()]

    queues = [Queue(name, connection=redis_conn) for name in listen]
    worker = Worker(queues, connection=redis_conn)
    logger.info("RQ worker polling queues: %s (isolation = job.owner_id)", ", ".join(listen))
    worker.work()


if __name__ == "__main__":
    main()
