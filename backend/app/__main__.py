"""Start the server with:  python -m app

On Windows, plain `uvicorn` installs an event loop that psycopg's async driver refuses to use.
Importing this package first selects a compatible loop, and loop="none" stops uvicorn replacing it.
"""
import logging
import os

import uvicorn

import app  # noqa: F401  (importing the package picks the Windows-compatible event loop)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        loop="none",
    )
