-- Per-agency prices on top of the shared catalogue (docs/PLAN_NEXT_SAAS.md, feature 2).
--
-- The shared price (properties.price_etb, projects.price_from_etb) stays the default and only the platform writes it. An agency may set
-- its own price for a unit or a project (a price it negotiated, or one for a project that has none). Everything that shows or compares a
-- price reads it through the views below, which put the agency's own price in the usual column (`price_etb`, `price_from_etb`) and keep
-- the shared one beside it. The views run as the caller (`security_invoker`), and the join names `current_org()` as well, so the price a
-- caller sees is its own agency's and no other's, whether it runs as an agency's role or as the platform.

create table if not exists agency_prices (
  org_id       uuid not null default current_org() references organizations(id) on delete cascade,
  target_kind  text not null check (target_kind in ('project', 'property')),
  target_id    uuid not null,
  price_etb    numeric(14, 2) not null check (price_etb > 0 and price_etb < 1e12),
  note         text check (length(note) <= 200),
  valid_until  date,
  created_by   uuid,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (org_id, target_kind, target_id)
);
-- No index on (target_kind, target_id): with it the planner fetched every agency's price for a unit and filtered, slower than the
-- primary key, which finds this agency's one row directly (docs/BENCHMARKS.md).

alter table agency_prices enable row level security;
grant select, insert, update, delete on agency_prices to app_user;
drop policy if exists tenant_isolation on agency_prices;
create policy tenant_isolation on agency_prices for all to app_user using (org_id = current_org()) with check (org_id = current_org());

-- An agency's price is in force until the end of its `valid_until` day, or for good when there is none.
create or replace view agency_properties with (security_invoker = true) as
select p.id, p.developer_id, p.title, p.project_name, p.unit_type, p.sub_city, p.lat, p.lng,
       coalesce(ap.price_etb, p.price_etb) as price_etb,
       p.bedrooms, p.sqm, p.down_payment_pct, p.construction_stage_pct, p.est_rental_yield_pct, p.delivery_date, p.status, p.description,
       p.floor_plan_image_url, p.vision_amenities, p.content_hash, p.embedding, p.created_at, p.updated_at, p.payment_plan, p.source_url,
       p.photo_url, p.org_id,
       p.price_etb as shared_price_etb, ap.price_etb as agency_price_etb, ap.note as agency_price_note, ap.valid_until as agency_price_until
from properties p
left join agency_prices ap
  on ap.org_id = current_org() and ap.target_kind = 'property' and ap.target_id = p.id
 and (ap.valid_until is null or ap.valid_until >= current_date);

create or replace view agency_projects with (security_invoker = true) as
select pr.id, pr.developer_id, pr.name, pr.slug, pr.source_url, pr.sub_city, pr.location_text, pr.summary, pr.bedrooms_min, pr.bedrooms_max,
       pr.sqm_min, pr.sqm_max, pr.floors, pr.status,
       coalesce(ap.price_etb, pr.price_from_etb) as price_from_etb,
       case when ap.price_etb is not null then ap.note else pr.price_note end as price_note,
       pr.image_url, pr.promo_text, pr.promo_discount_pct, pr.promo_until, pr.content_hash, pr.last_seen_at, pr.created_at, pr.updated_at,
       pr.embedding,
       pr.price_from_etb as shared_price_from_etb, pr.price_note as shared_price_note,
       ap.price_etb as agency_price_etb, ap.note as agency_price_note, ap.valid_until as agency_price_until
from projects pr
left join agency_prices ap
  on ap.org_id = current_org() and ap.target_kind = 'project' and ap.target_id = pr.id
 and (ap.valid_until is null or ap.valid_until >= current_date);

grant select on agency_properties, agency_projects to app_user;

-- One number, for code that needs only the price.
create or replace function effective_price(p_kind text, p_id uuid) returns numeric
language sql stable security invoker as $$
  select case p_kind
    when 'property' then (select price_etb from agency_properties where id = p_id)
    when 'project'  then (select price_from_etb from agency_projects where id = p_id)
  end
$$;

-- The searches compare budgets with, and return, the agency's price.
create or replace function match_properties_hybrid(
  query_embedding vector, p_max_budget_etb double precision, p_min_bedrooms integer, p_sub_cities text[],
  p_match_count integer default 3, p_similarity_threshold double precision default 0.65
) returns table (id uuid, developer_id uuid, title text, sub_city text, price_etb double precision, bedrooms integer, similarity double precision)
language plpgsql stable as $$
begin
  return query
  select p.id,
         p.developer_id,
         p.title,
         p.sub_city,
         p.price_etb::float,
         p.bedrooms,
         (1 - (p.embedding <=> query_embedding))::float as similarity
  from agency_properties p
  where p.status = 'AVAILABLE'
    and p.embedding is not null
    and (p_max_budget_etb is null or p.price_etb <= p_max_budget_etb)
    and (p_min_bedrooms   is null or p.bedrooms  >= p_min_bedrooms)
    and (array_length(p_sub_cities, 1) is null or p.sub_city = any(p_sub_cities))
    and (1 - (p.embedding <=> query_embedding)) >= p_similarity_threshold
  order by p.embedding <=> query_embedding
  limit p_match_count;
end $$;

create or replace function match_buyers_for_property(p_property_id uuid, p_similarity_threshold double precision default 0.64, p_limit integer default 50)
returns table (buyer_id uuid, similarity double precision, human_takeover boolean)
language sql stable as $$
  select b.id,
         (1 - (b.preference_embedding <=> p.embedding))::float,
         b.human_takeover
  from agency_properties p
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
