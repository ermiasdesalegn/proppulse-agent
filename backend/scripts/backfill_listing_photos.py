"""Give listings crawled before photos were kept their cover photo, read again from their own portal page.
Usage:  python -m scripts.backfill_listing_photos  [--dry-run] [--recheck]

--recheck also revisits listings that already have a photo, so one the site has since deleted is replaced (or removed).
One site at a time is asked one page every few seconds (sites in parallel). Each photo is loaded once before it is kept; when the
main one is gone, the other sizes the site keeps are tried. Listings left without a photo keep the website's drawing.
Later crawls fill in photos by themselves."""
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


async def working_photo(fetcher: HttpFetcher, candidates: list[str]) -> str | None:
    for url in candidates:
        if not url.startswith(("http://", "https://")) or len(url) > 2048:
            continue
        try:
            await fetcher.get_bytes(url)
            return url
        except FetchError:
            continue
    return None


async def backfill_site(repo: Repository, fetcher: HttpFetcher, slug: str, rows: list[dict], dry_run: bool) -> None:
    hints = SITES[slug].hints if slug in SITES else None
    found = failed = removed = 0
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
        listing = extract_listing(html, row["source_url"], hints)
        photo = await working_photo(fetcher, [listing.image_url, *listing.image_alternatives]) if listing.image_url else None
        if photo:
            found += 1
            if not dry_run and photo != row.get("photo_url"):
                await repo.update_property_photo(row["id"], photo)
        elif row.get("photo_url"):
            removed += 1  # the stored photo no longer loads and nothing replaces it: the drawing is better than a broken picture
            if not dry_run:
                await repo.update_property_photo(row["id"], None)
    print(f"{slug}: {found} working photos from {len(rows)} listings ({removed} removed, {failed} pages failed)")


async def main(dry_run: bool, recheck: bool) -> None:
    settings = get_settings()
    pool = make_pool(settings)
    await pool.open(wait=True, timeout=30)
    repo, fetcher = Repository(pool), HttpFetcher()
    try:
        by_site: dict[str, list[dict]] = defaultdict(list)
        for row in await repo.properties_missing_photo(include_existing=recheck):
            by_site[row["developer_slug"]].append(row)
        print({slug: len(rows) for slug, rows in by_site.items()})
        await asyncio.gather(*(backfill_site(repo, fetcher, slug, rows, dry_run) for slug, rows in by_site.items()))
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.recheck))
