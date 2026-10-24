-- Listings read by the Scrapy portal crawler (scraper/) arrive as their own ingestion source.

alter table ingest_runs drop constraint if exists ingest_runs_source_check;
alter table ingest_runs add constraint ingest_runs_source_check check (source in ('html', 'text', 'url', 'scheduled', 'portal'));
