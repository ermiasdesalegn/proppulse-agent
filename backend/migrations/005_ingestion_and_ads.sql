-- Market ingestion (scraper) and Telegram channel ads.

alter table developers
  add column scrape_url     text,    -- the developer's public listing page, fetched by the scraper
  add column scrape_profile jsonb;   -- CSS selectors that say where each field lives on that page

-- One row per ingestion run, for the Studio's history and for debugging a bad scrape.
create table ingest_runs (
  id           bigint generated always as identity primary key,
  developer_id uuid references developers(id) on delete set null,
  source       text not null check (source in ('html', 'text', 'url', 'scheduled')),
  dry_run      boolean not null default false,
  full_sync    boolean not null default false,
  created      int not null default 0,
  price_changed int not null default 0,
  removed      int not null default 0,
  unchanged    int not null default 0,
  skipped      int not null default 0,
  report       jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);
create index ingest_runs_recent_idx on ingest_runs (created_at desc);

-- Every ad posted to the public Telegram channel. The unique index stops the same event being announced twice.
create table channel_posts (
  id                 bigint generated always as identity primary key,
  property_id        uuid not null references properties(id) on delete cascade,
  kind               text not null check (kind in ('NEW_LISTING', 'PRICE_DROP', 'MANUAL')),
  price_etb          numeric(14,2) not null,
  chat_id            text not null,
  telegram_message_id bigint,
  text               text not null,
  created_at         timestamptz not null default now()
);
create unique index channel_posts_once on channel_posts (property_id, kind, price_etb) where kind <> 'MANUAL';
create index channel_posts_recent_idx on channel_posts (created_at desc);

alter table ingest_runs  enable row level security;
alter table channel_posts enable row level security;
