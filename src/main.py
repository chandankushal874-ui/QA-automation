"""Cross-Browser Testing Platform — Main Entry Point.

Starts the FastAPI backend server.
Usage: python -m src.main
       or: uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
"""

import uvicorn


def main():
    """Run the FastAPI server."""
    uvicorn.run(
        "src.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
