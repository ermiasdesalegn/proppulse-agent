"""Add the demo projects (developers that publish no prices) to the configured database.  Usage:  python -m scripts.seed_projects"""
import asyncio

from app.config import get_settings
from app.db import make_pool
from app.demo_projects import restore_demo_projects
from app.gemini import get_gateway
from app.projects import embedding_text
from app.repository import Repository


async def main() -> None:
    settings = get_settings()
    pool = make_pool(settings)
    await pool.open(wait=True, timeout=30)
    repo, gateway = Repository(pool), get_gateway()

    async def embed(project: dict) -> None:
        (vector,) = await gateway.embed_documents([embedding_text(project)])
        await repo.set_project_embedding(project["id"], vector)

    try:
        print(await restore_demo_projects(repo, embed))
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
