-- Tell buyers when an agency lowers its own price for a unit (docs/PLAN_REMAINING_SAAS.md, item 2).
--
-- price_history records only the shared price. An agency's own unit price (agency_prices, migration 028) changes what that agency shows,
-- so its changes go in a private table of their own, written by a trigger on agency_prices. The reverse-RAG alerts read both.

create table if not exists agency_price_history (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null default current_org() references organizations(id) on delete cascade,
  property_id   uuid not null references properties(id) on delete cascade,
  old_price_etb numeric(14, 2) not null,
  new_price_etb numeric(14, 2) not null,
  change_pct    numeric(7, 2) not null,
  detected_at   timestamptz not null default now()
);
create index if not exists agency_price_history_idx on agency_price_history (org_id, detected_at desc);

alter table agency_price_history enable row level security;
grant select, insert, update, delete on agency_price_history to app_user;
drop policy if exists tenant_isolation on agency_price_history;
create policy tenant_isolation on agency_price_history for all to app_user using (org_id = current_org()) with check (org_id = current_org());

-- The price the agency showed before and after is its own price while that is in force (no date, or the date has not passed), else the
-- shared one. Setting, changing, clearing, or replacing an already expired price is a change only when those two numbers differ.
create or replace function record_agency_price_change() returns trigger language plpgsql as $$
declare
  rec     agency_prices;
  shared  numeric;
  was     numeric;
  now_    numeric;
begin
  rec := case when tg_op = 'DELETE' then old else new end;
  -- An agency removed with its prices is not a price change.
  if rec.target_kind <> 'property' or (tg_op = 'DELETE' and pg_trigger_depth() > 1) then
    return rec;
  end if;
  select price_etb into shared from properties where id = rec.target_id;
  if shared is null then
    return rec;
  end if;
  was  := case when tg_op <> 'INSERT' and (old.valid_until is null or old.valid_until >= current_date) then old.price_etb else shared end;
  now_ := case when tg_op <> 'DELETE' and (new.valid_until is null or new.valid_until >= current_date) then new.price_etb else shared end;
  if was <> now_ then
    insert into agency_price_history (org_id, property_id, old_price_etb, new_price_etb, change_pct)
    values (rec.org_id, rec.target_id, was, now_, round((now_ - was) / was * 100, 2));
  end if;
  return rec;
end $$;

drop trigger if exists agency_prices_history on agency_prices;
create trigger agency_prices_history after insert or update or delete on agency_prices
  for each row execute function record_agency_price_change();
