-- PropPulse AI: core schema (single-tenant)

create extension if not exists vector;

create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- Partner developers with a signed brokerage mandate
create table developers (
  id                  uuid primary key default gen_random_uuid(),
  name                text not null,
  slug                text not null unique,
  sales_email         text not null,
  commission_rate_pct numeric(5,2) not null check (commission_rate_pct between 0 and 100),
  reliability_score   numeric(3,2) not null default 0.50 check (reliability_score between 0 and 1),
  website_url         text,
  created_at          timestamptz not null default now()
);

-- One row per representative unit model, not per physical door
create table properties (
  id                    uuid primary key default gen_random_uuid(),
  developer_id          uuid not null references developers(id),
  title                 text not null,
  project_name          text not null,
  unit_type             text not null,
  sub_city              text not null,
  lat                   double precision,
  lng                   double precision,
  price_etb             numeric(14,2) not null check (price_etb > 0),
  bedrooms              int not null check (bedrooms >= 0),
  sqm                   numeric(7,2) not null check (sqm > 0),
  down_payment_pct      numeric(5,2),
  construction_stage_pct numeric(5,2) check (construction_stage_pct between 0 and 100),
  est_rental_yield_pct  numeric(5,2),
  delivery_date         date,
  status                text not null default 'AVAILABLE' check (status in ('AVAILABLE','UNAVAILABLE')),
  description           text,
  floor_plan_image_url  text,
  vision_amenities      jsonb not null default '{}'::jsonb,
  content_hash          text not null unique,
  embedding             vector(768),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index properties_embedding_hnsw on properties
  using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);
create index properties_developer_idx on properties (developer_id);
create index properties_filter_idx on properties (status, sub_city);
create trigger properties_updated before update on properties
  for each row execute function set_updated_at();

create table price_history (
  id            uuid primary key default gen_random_uuid(),
  property_id   uuid not null references properties(id) on delete cascade,
  old_price_etb numeric(14,2) not null,
  new_price_etb numeric(14,2) not null,
  change_pct    numeric(7,2) not null,
  detected_at   timestamptz not null default now()
);
create index price_history_property_idx on price_history (property_id, detected_at desc);

-- Smart Phonebook CRM
create table buyers_crm (
  id                   uuid primary key default gen_random_uuid(),
  thread_id            text not null unique,        -- LangGraph thread key: 'tg:<id>' or 'web:<uuid>'
  telegram_id          bigint unique,
  full_name            text,
  phone                text,
  email                text,
  max_budget_etb       numeric(14,2),
  min_bedrooms         int,
  preferred_sub_cities text[] not null default '{}',
  lead_score           int not null default 0 check (lead_score between 0 and 100),
  deal_stage           text not null default 'NEW' check (deal_stage in (
                         'NEW','QUALIFIED','INVENTORY_PENDING','SITE_VISIT_BOOKED',
                         'TOUR_COMPLETED','CONTRACT_SIGNED','COMMISSION_PENDING',
                         'COMMISSION_PAID','LOST')),
  human_takeover       boolean not null default false,
  preference_summary   text,
  preference_embedding vector(768),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
create trigger buyers_crm_updated before update on buyers_crm
  for each row execute function set_updated_at();

create table tours_and_appointments (
  id                uuid primary key default gen_random_uuid(),
  buyer_id          uuid not null references buyers_crm(id) on delete cascade,
  scheduled_start   timestamptz not null,
  calendar_event_id text,
  mapbox_route_json jsonb,        -- {geometry, distance_meters, duration_seconds, bbox}
  reminder_sent     boolean not null default false,
  created_at        timestamptz not null default now()
);
create index tours_buyer_idx on tours_and_appointments (buyer_id);
create index tours_start_idx on tours_and_appointments (scheduled_start);

create table tour_properties (
  tour_id     uuid not null references tours_and_appointments(id) on delete cascade,
  property_id uuid not null references properties(id),
  stop_order  int not null check (stop_order >= 1),
  primary key (tour_id, property_id)
);
create index tour_properties_property_idx on tour_properties (property_id);

-- One row per developer opportunity
create table commission_ledger (
  id                        uuid primary key default gen_random_uuid(),
  buyer_id                  uuid not null references buyers_crm(id),
  developer_id              uuid not null references developers(id),
  property_id               uuid not null references properties(id),
  tour_id                   uuid references tours_and_appointments(id) on delete set null,
  lead_reg_email_id         text,
  lead_registered_at        timestamptz,
  contract_value_etb        numeric(14,2),
  expected_commission_etb   numeric(14,2),
  next_buyer_milestone_date date,
  status                    text not null default 'LEAD_REGISTERED' check (status in (
                              'LEAD_REGISTERED','TOUR_COMPLETED','CONTRACT_SIGNED',
                              'COMMISSION_PENDING','COMMISSION_PAID','CLOSED_LOST')),
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now()
);
create index ledger_buyer_idx on commission_ledger (buyer_id);
create index ledger_status_idx on commission_ledger (status);
create trigger ledger_updated before update on commission_ledger
  for each row execute function set_updated_at();

-- Backend uses the service role / direct connection; block the public API by default.
alter table developers             enable row level security;
alter table properties             enable row level security;
alter table price_history          enable row level security;
alter table buyers_crm             enable row level security;
alter table tours_and_appointments enable row level security;
alter table tour_properties        enable row level security;
alter table commission_ledger      enable row level security;
