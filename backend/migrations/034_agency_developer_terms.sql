-- An agency's own commission terms with a developer (docs/PLAN_PRODUCT_AND_MONEY.md, phase 1, item 1).
--
-- The developer's rate (developers.commission_rate_pct) stays the shared default and only the platform writes it. An agency may record the
-- rate it agreed with a developer. From then on its expected commissions, dossiers and analytics use that rate, and no other agency sees it.
-- Rows already in the ledger keep the rate they were created with.

create table if not exists agency_developer_terms (
  org_id        uuid not null default current_org() references organizations(id) on delete cascade,
  developer_id  uuid not null references developers(id) on delete cascade,
  rate_pct      numeric(5, 2) not null check (rate_pct between 0 and 100),
  note          text check (length(note) <= 200),
  valid_until   date,
  created_by    uuid,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (org_id, developer_id)
);

alter table agency_developer_terms enable row level security;
grant select, insert, update, delete on agency_developer_terms to app_user;
drop policy if exists tenant_isolation on agency_developer_terms;
create policy tenant_isolation on agency_developer_terms for all to app_user using (org_id = current_org()) with check (org_id = current_org());

-- The rate in force for the caller's agency: its own term while that is in force (no date, or the date has not passed), else the shared one.
-- Runs as the caller, like effective_price: an agency's role sees its own terms only, the platform (no agency in scope) the shared rate.
create or replace function effective_commission_rate(p_developer uuid) returns numeric
language sql stable security invoker as $$
  select coalesce(
    (select t.rate_pct from agency_developer_terms t
      where t.org_id = current_org() and t.developer_id = p_developer and (t.valid_until is null or t.valid_until >= current_date)),
    (select d.commission_rate_pct from developers d where d.id = p_developer))
$$;
