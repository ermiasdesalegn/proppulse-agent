-- Fix for migration 029: deleting an agency that has its own unit prices failed. The delete cascades to agency_prices, whose trigger then
-- tried to write a history row for the agency being deleted (a foreign key violation) and the whole delete was refused.
--
-- The trigger now leaves the history alone when the agency is already gone. It runs as the writer, like before (no function in this database runs with
-- its owner's rights): an agency's role can read its own organization row, which is the only one a price row can name, and the platform sees all.

create or replace function record_agency_price_change() returns trigger language plpgsql as $$
declare
  rec     agency_prices;
  shared  numeric;
  was     numeric;
  now_    numeric;
begin
  rec := case when tg_op = 'DELETE' then old else new end;
  if rec.target_kind <> 'property' then
    return rec;
  end if;
  -- An agency removed with its prices is not a price change.
  if tg_op = 'DELETE' and not exists (select 1 from organizations where id = rec.org_id) then
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
