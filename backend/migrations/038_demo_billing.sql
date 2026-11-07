-- Phase 2 of docs/PLAN_PRODUCT_AND_MONEY.md: the platform earning money, as a DEMO. Expand only.
--
-- Nothing here moves real money. A "payment" is a click on a mock checkout (app/billing.py, MockPaymentProvider); an invoice row is the
-- receipt for it and says it is a demo. The prices and fee percentages below are PLACEHOLDERS the owner has not confirmed: change them
-- here (one table), nothing else holds a price.
--
-- plan_prices     what each plan costs per month and what share of an agency's PAID commission the platform keeps (readable by agencies)
-- invoices        one receipt per plan purchase (written by the platform after the mock payment; an agency can only read its own)
-- platform_fees   one row per deal that reached COMMISSION_PAID, written by a trigger so it is neither lost nor duplicated

create table if not exists plan_prices (
  plan       text primary key check (plan in ('free', 'pro', 'business')),
  rank       smallint not null unique,
  price_etb  numeric(12, 2) not null check (price_etb >= 0),
  fee_pct    numeric(5, 2) not null check (fee_pct between 0 and 100)
);
insert into plan_prices (plan, rank, price_etb, fee_pct) values ('free', 0, 0, 10), ('pro', 1, 2500, 5), ('business', 2, 9000, 2)
  on conflict (plan) do nothing;
alter table plan_prices enable row level security;
grant select on plan_prices to app_user;
drop policy if exists plan_prices_read on plan_prices;
create policy plan_prices_read on plan_prices for select to app_user using (true);

create table if not exists invoices (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null default current_org() references organizations(id) on delete cascade,
  number      bigint generated always as identity,
  kind        text not null default 'subscription' check (kind in ('subscription')),
  from_plan   text not null,
  plan        text not null check (plan in ('free', 'pro', 'business')),
  amount_etb  numeric(12, 2) not null check (amount_etb >= 0),
  demo        boolean not null default true,
  created_by  uuid,
  created_at  timestamptz not null default now()
);
create index if not exists invoices_org_idx on invoices (org_id, created_at desc);
alter table invoices enable row level security;
grant select on invoices to app_user;
drop policy if exists tenant_read on invoices;
create policy tenant_read on invoices for select to app_user using (org_id = current_org());

create table if not exists platform_fees (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null references organizations(id) on delete cascade,
  ledger_id      uuid not null unique references commission_ledger(id) on delete cascade,  -- once per deal, for ever
  plan           text not null,
  fee_pct        numeric(5, 2) not null,
  commission_etb numeric(14, 2) not null,
  fee_etb        numeric(14, 2) not null,
  demo           boolean not null default true,
  created_at     timestamptz not null default now()
);
create index if not exists platform_fees_org_idx on platform_fees (org_id, created_at desc);
alter table platform_fees enable row level security;
grant select, insert on platform_fees to app_user;
drop policy if exists tenant_read on platform_fees;
create policy tenant_read on platform_fees for select to app_user using (org_id = current_org());
-- An agency's role may add a fee row only from inside the trigger below (pg_trigger_depth() > 0), never with a statement of its own.
drop policy if exists trigger_insert on platform_fees;
create policy trigger_insert on platform_fees for insert to app_user with check (org_id = current_org() and pg_trigger_depth() > 0);

-- Runs as whoever moved the deal (no SECURITY DEFINER in this schema, migration 022's rule), for whichever path moved it. The percentage is
-- the agency's plan at that moment. `on conflict do nothing` plus the unique ledger_id keep a deal that is moved out of and back into PAID from
-- being charged twice.
create or replace function ledger_write_platform_fee() returns trigger language plpgsql as $$
declare
  v_plan text;
  v_pct  numeric(5, 2);
begin
  if new.status = 'COMMISSION_PAID' and (tg_op = 'INSERT' or old.status is distinct from 'COMMISSION_PAID') then
    select o.plan, coalesce(p.fee_pct, 0) into v_plan, v_pct from organizations o left join plan_prices p on p.plan = o.plan where o.id = new.org_id;
    insert into platform_fees (org_id, ledger_id, plan, fee_pct, commission_etb, fee_etb)
    values (new.org_id, new.id, coalesce(v_plan, 'free'), coalesce(v_pct, 0), coalesce(new.expected_commission_etb, 0),
            round(coalesce(new.expected_commission_etb, 0) * coalesce(v_pct, 0) / 100, 2))
    on conflict (ledger_id) do nothing;
  end if;
  return new;
end $$;
drop trigger if exists ledger_platform_fee on commission_ledger;
create trigger ledger_platform_fee after insert or update on commission_ledger for each row execute function ledger_write_platform_fee();
