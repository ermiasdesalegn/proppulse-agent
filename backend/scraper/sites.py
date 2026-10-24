"""The portals the crawler reads, and what is special about each.

Checked by hand on 2026-10-24 (robots.txt, sitemap and one listing page each). Every site allows general crawlers in robots.txt. To add a
portal: find its sitemap (robots.txt usually names it) or a listing index page, write the pattern of a listing address, open one listing
and see whether `extract_listing` already reads it (`scrapy parse --spider=portals -a site=<slug> <url>`); add CSS hints only for the
fields it misses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingest.portal import SiteHints


@dataclass(frozen=True)
class Site:
    slug: str
    name: str
    listing: re.Pattern[str]                                  # the address of one listing page
    sitemaps: tuple[str, ...] = ()                            # preferred: every listing address at once
    sitemap_follow: re.Pattern[str] | None = None             # which child sitemaps of an index to open (None: all of them)
    index_pages: tuple[str, ...] = ()                         # for sites without a usable sitemap: listing index pages to walk
    next_page: re.Pattern[str] | None = None                  # pagination links on an index page
    render: bool = False                                      # the page builds its content with JavaScript: render it in Playwright
    allow_llm: bool = True                                    # may a page without a strict price be sent to the language model
    hints: SiteHints = field(default_factory=SiteHints)
    note: str = ""


SITES: dict[str, Site] = {site.slug: site for site in (
    Site(
        slug="ethiopiapropertycentre", name="Ethiopia Property Centre",
        sitemaps=("https://st.ethiopiapropertycentre.com/sitemaps/index.xml",),  # robots.txt names ethiopiapropertycentre.com/sitemaps/index.xml, which redirects here
        sitemap_follow=re.compile(r"sitemap_listings"),
        listing=re.compile(r"ethiopiapropertycentre\.com/for-sale/(flats-apartments|houses)/[^?#]+/\d+-[^/?#]+$"),
        note="Plain-text sitemaps; JSON-LD carries the price, the visible details carry size and bedrooms.",
    ),
    Site(
        slug="livingethio", name="Live Ethio",
        sitemaps=("https://livingethio.com/property-sitemap.xml",),
        listing=re.compile(r"livingethio\.com/site/property-details/\d+$"),
        note="Angular app rendered on the server; the listing record is in its transfer-state JSON. Rentals are only known after reading.",
    ),
    Site(
        slug="guzohomes", name="GuzoHomes",
        sitemaps=("https://guzohomes.com/sitemap.xml",),
        listing=re.compile(r"guzohomes\.com/property/[a-z0-9]+$"),
        hints=SiteHints(ref_pattern=r"/property/([a-z0-9]+)"),
        note="Next.js; complete RealEstateListing JSON-LD on every listing.",
    ),
    Site(
        slug="addislist", name="Addis List",
        sitemaps=("https://addislist.com/sitemap_index.xml",),
        sitemap_follow=re.compile(r"property-sitemap\d*\.xml"),
        listing=re.compile(r"addislist\.com/property/[^/?#]+/?$"),
        hints=SiteHints(css={"price": "ul.property-price-wrap li.item-price span.price", "sub_city": "address.item-address", "deal": "a.label-status"}),
        note="WordPress (Houzez theme). Cloudflare sometimes answers with a bot challenge; the crawler then stops for this site and tries again next run.",
    ),
    Site(
        slug="ethiobetoch", name="Ethio Betoch",
        index_pages=("https://ethiobetoch.com/propertylisting",),
        next_page=re.compile(r"ethiobetoch\.com/propertylisting\?page=\d+$"),
        listing=re.compile(r"ethiobetoch\.com/property/[^?#]+-\d+$"),
        hints=SiteHints(css={"title": "div.text-black.font-bold.text-xl", "price": "div.text-blue-600 p.text-xs"}),
        note="No sitemap. The headline shows 'Br 200' with the unit ('200 million') underneath, so the price is read from that line.",
    ),
    Site(
        slug="ethiohome", name="EthioHome",
        index_pages=("https://ethiohome.et/search?listingType=SALE",),
        next_page=re.compile(r"ethiohome\.et/search\?[^#]*page=\d+"),
        listing=re.compile(r"ethiohome\.et/properties/[a-z0-9]+$"),
        render=True,
        allow_llm=False,  # robots.txt carries Content-Signal (ai-train=no, use=reference): keep its pages away from the model altogether
        hints=SiteHints(css={"title": "main h1", "price": "main p.tabular-nums"}, ref_pattern=r"/properties/([a-z0-9]+)"),
        note="Single-page app with no sitemap and no server-rendered content: needs Playwright.",
    ),
)}
