"""Give listings crawled before photos were kept their cover photo, read again from their own portal page.
Usage:  python -m scripts.backfill_listing_photos  [--dry-run]

One site at a time is asked one page every few seconds (sites in parallel). Pages that fail or show no photo are left alone;
the website keeps drawing those. Later crawls fill in photos by themselves."""
import argparse
import asyncio
from collections import defaultdict

from app.config import get_settings
from app.db import make_pool
from app.ingest.fetch import FetchError, HttpFetcher
from app.ingest.portal import extract_listing, is_challenge_page
from app.repository import Repository
from scraper.sites import SITES

PAUSE_SECONDS = 2.0


async def backfill_site(repo: Repository, fetcher: HttpFetcher, slug: str, rows: list[dict], dry_run: bool) -> tuple[int, int]:
    hints = SITES[slug].hints if slug in SITES else None
    found = failed = 0
    for row in rows:
        try:
            html = await fetcher.get_text(row["source_url"], polite=False)
        except FetchError as exc:
            failed += 1
            if failed == 5 and found == 0:
                print(f"{slug}: stopping, the first pages all failed ({exc})")
                break
            continue
        finally:
            await asyncio.sleep(PAUSE_SECONDS)
        if is_challenge_page(html):
            print(f"{slug}: stopping, the site shows a bot check (not worked around)")
            break
        photo = extract_listing(html, row["source_url"], hints).image_url
        if photo and photo.startswith(("http://", "https://")) and len(photo) <= 2048:
            found += 1
            if not dry_run:
                await repo.update_property_photo(row["id"], photo)
    print(f"{slug}: {found} photos from {len(rows)} listings ({failed} pages failed)")
    return found, len(rows)


async def main(dry_run: bool) -> None:
    settings = get_settings()
    pool = make_pool(settings)
    await pool.open(wait=True, timeout=30)
    repo, fetcher = Repository(pool), HttpFetcher()
    try:
        by_site: dict[str, list[dict]] = defaultdict(list)
        for row in await repo.properties_missing_photo():
            by_site[row["developer_slug"]].append(row)
        print({slug: len(rows) for slug, rows in by_site.items()})
        await asyncio.gather(*(backfill_site(repo, fetcher, slug, rows, dry_run) for slug, rows in by_site.items()))
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(parser.parse_args().dry_run))
