-- Deal assignment and split (docs/PLAN_PRODUCT_AND_MONEY.md, phase 1, item 4). Expand only.
--
-- assigned_to is the agency member who owns a buyer or a deal (a user id; the member is checked by the API, and a member who later
-- leaves keeps their past assignments, which is the record of who worked the deal). commission_splits divides one deal's commission
-- between members: percent per member, and the percents of a deal add up to 100 (checked by the database at commit).

alter table buyers_crm        add column if not exists assigned_to uuid;
alter table commission_ledger add column if not exists assigned_to uuid;
create index if not exists buyers_assigned_idx on buyers_crm (assigned_to) where assigned_to is not null;
create index if not exists ledger_assigned_idx on commission_ledger (assigned_to) where assigned_to is not null;

create table if not exists commission_splits (
  org_id      uuid not null default current_org() references organizations(id) on delete cascade,
  ledger_id   uuid not null references commission_ledger(id) on delete cascade,
  user_id     uuid not null,
  percent     numeric(5, 2) not null check (percent > 0 and percent <= 100),
  created_at  timestamptz not null default now(),
  primary key (ledger_id, user_id)
);

alter table commission_splits enable row level security;
grant select, insert, update, delete on commission_splits to app_user;
drop policy if exists tenant_isolation on commission_splits;
create policy tenant_isolation on commission_splits for all to app_user using (org_id = current_org()) with check (org_id = current_org());

-- A deal either has no split or a split that adds up to exactly 100. Checked when the transaction commits, so a whole new split can be
-- written row by row.
create or replace function commission_split_total() returns trigger language plpgsql as $$
declare
  deal  uuid := coalesce(new.ledger_id, old.ledger_id);
  total numeric;
  n     int;
begin
  select coalesce(sum(percent), 0), count(*) into total, n from commission_splits where ledger_id = deal;
  if n > 0 and total <> 100 then
    raise exception 'the split of deal % adds up to % percent, not 100', deal, total using errcode = 'check_violation';
  end if;
  return null;
end $$;
drop trigger if exists commission_split_sums on commission_splits;
create constraint trigger commission_split_sums after insert or update or delete on commission_splits
  deferrable initially deferred for each row execute function commission_split_total();
