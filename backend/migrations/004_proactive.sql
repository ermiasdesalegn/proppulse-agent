-- Proactive jobs: a ledger of everything the system sent on its own (also the idempotency guard and the broker's notice feed),
-- and the reverse of match_properties_hybrid: which buyers does a property match?

create table proactive_events (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in (
                'PRICE_DROP', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
                'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH')),
  audience    text not null check (audience in ('buyer', 'broker')),
  buyer_id    uuid references buyers_crm(id) on delete cascade,
  property_id uuid references properties(id) on delete set null,
  tour_id     uuid references tours_and_appointments(id) on delete set null,
  ledger_id   uuid references commission_ledger(id) on delete set null,
  dedupe_key  text not null unique,   -- one row per (event, recipient): sending twice is impossible
  message     text,
  channel     text,                   -- telegram, email, telegram+email, web, feed
  status      text not null default 'pending' check (status in ('pending', 'sent')),
  created_at  timestamptz not null default now()
);
create index proactive_events_buyer_idx on proactive_events (buyer_id, created_at desc);
create index proactive_events_feed_idx on proactive_events (audience, created_at desc);

alter table proactive_events enable row level security;

do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table proactive_events;
  end if;
end $$;

-- Reverse retrieval: same hard filters and cosine floor as the forward search, applied from the property's side.
-- Buyers who already own a deal (signed, paid, lost) or have not told us anything yet are never contacted.
create or replace function match_buyers_for_property(
  p_property_id          uuid,
  p_similarity_threshold float default 0.64,
  p_limit                int   default 50
)
returns table (buyer_id uuid, similarity float, human_takeover boolean)
language sql stable
as $$
  select b.id,
         (1 - (b.preference_embedding <=> p.embedding))::float,
         b.human_takeover
  from properties p
  join buyers_crm b on b.preference_embedding is not null
  where p.id = p_property_id
    and p.status = 'AVAILABLE'
    and p.embedding is not null
    and b.deal_stage in ('QUALIFIED', 'INVENTORY_PENDING', 'SITE_VISIT_BOOKED', 'TOUR_COMPLETED')
    and (b.max_budget_etb is null or p.price_etb <= b.max_budget_etb)
    and (b.min_bedrooms   is null or p.bedrooms  >= b.min_bedrooms)
    and (cardinality(b.preferred_sub_cities) = 0 or p.sub_city = any(b.preferred_sub_cities))
    and (1 - (b.preference_embedding <=> p.embedding)) >= p_similarity_threshold
  order by 2 desc
  limit p_limit;
$$;
