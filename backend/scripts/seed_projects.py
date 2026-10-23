"""Add the demo projects (developers that publish no prices) to the configured database.  Usage:  python -m scripts.seed_projects"""
import asyncio

from app.config import get_settings
from app.db import make_pool
from app.demo_projects import restore_demo_projects
from app.repository import Repository


async def main() -> None:
    pool = make_pool(get_settings())
    await pool.open(wait=True, timeout=30)
    try:
        print(await restore_demo_projects(Repository(pool)))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
