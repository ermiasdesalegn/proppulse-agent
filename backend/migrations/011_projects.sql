-- Projects: what developers publish when they do not publish unit prices ("price on request").
-- A buyer can ask the bot for the price; the broker gets the request, asks the developer, enters the price, and every waiting buyer is told.

create table projects (
  id                 uuid primary key default gen_random_uuid(),
  developer_id       uuid not null references developers(id) on delete cascade,
  name               text not null,
  slug               text not null,
  source_url         text not null,
  sub_city           text,
  location_text      text,
  summary            text,
  bedrooms_min       int,
  bedrooms_max       int,
  sqm_min            numeric(8,2),
  sqm_max            numeric(8,2),
  floors             int,
  status             text not null default 'SELLING' check (status in ('SELLING', 'SOLD_OUT', 'UPCOMING')),
  price_from_etb     numeric(14,2),            -- null means price on request
  price_note         text,
  image_url          text,
  promo_text         text,                     -- the developer's current offer, in their words
  promo_discount_pct numeric(5,2),
  promo_until        date,
  content_hash       text not null,
  last_seen_at       timestamptz not null default now(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (developer_id, slug)
);
create index projects_area_idx on projects (sub_city) where status = 'SELLING';
create trigger projects_updated before update on projects for each row execute function set_updated_at();

create table price_requests (
  id           bigint generated always as identity primary key,
  project_id   uuid not null references projects(id) on delete cascade,
  buyer_id     uuid not null references buyers_crm(id) on delete cascade,
  status       text not null default 'OPEN' check (status in ('OPEN', 'ANSWERED')),
  created_at   timestamptz not null default now(),
  answered_at  timestamptz,
  unique (project_id, buyer_id)
);
create index price_requests_open_idx on price_requests (project_id) where status = 'OPEN';

-- Promotion ads posted to the channel, once per offer.
create table project_posts (
  id                  bigint generated always as identity primary key,
  project_id          uuid not null references projects(id) on delete cascade,
  kind                text not null check (kind in ('PROMO', 'MANUAL')),
  promo_key           text not null,           -- the offer as posted, so a changed offer is announced again
  chat_id             text not null,
  telegram_message_id bigint,
  text                text not null,
  created_at          timestamptz not null default now()
);
create unique index project_posts_once on project_posts (project_id, promo_key) where kind = 'PROMO';

alter table projects      enable row level security;
alter table price_requests enable row level security;
alter table project_posts  enable row level security;

-- The same two proactive notices need their kinds allowed.
alter table proactive_events drop constraint if exists proactive_events_kind_check;
alter table proactive_events add constraint proactive_events_kind_check check (kind in (
  'PRICE_DROP', 'PRICE_INCREASE', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
  'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH', 'PRICE_REQUEST', 'PROJECT_PRICE'));
