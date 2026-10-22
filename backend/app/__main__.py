"""Start the server with:  python -m app

On Windows, plain `uvicorn` installs an event loop that psycopg's async driver refuses to use.
Importing this package first selects a compatible loop, and loop="none" stops uvicorn replacing it.
"""
import os

import uvicorn

import app  # noqa: F401  (importing the package picks the Windows-compatible event loop)

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        loop="none",
    )
