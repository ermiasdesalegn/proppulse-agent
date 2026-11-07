-- Commission claims and reminders (docs/PLAN_PRODUCT_AND_MONEY.md, phase 1, item 3). Expand only.
--
-- A deal's stage (commission_ledger.status) is untouched, so code that does not know about claims keeps working. The claim is a second,
-- optional state on the same row: SENT (the agency emailed the developer), ACKNOWLEDGED (the developer confirmed), DISPUTED.
-- Every change is also written to commission_claim_events, so the deal keeps its own history besides the agency audit trail.

alter table commission_ledger add column if not exists claim_status     text check (claim_status in ('SENT', 'ACKNOWLEDGED', 'DISPUTED'));
alter table commission_ledger add column if not exists claim_sent_at    timestamptz;
alter table commission_ledger add column if not exists claim_to         text;
alter table commission_ledger add column if not exists claim_email_id   text;
alter table commission_ledger add column if not exists claim_updated_at timestamptz;
-- When the deal became COMMISSION_PENDING, and when the reminder job flagged it as late (null until it does).
alter table commission_ledger add column if not exists pending_since       timestamptz;
alter table commission_ledger add column if not exists overdue_flagged_at  timestamptz;

update commission_ledger set pending_since = updated_at where status = 'COMMISSION_PENDING' and pending_since is null;

-- Set by the database, so every path that moves a deal (the board, the agent, a script) records the day it became pending.
create or replace function ledger_track_pending() returns trigger language plpgsql as $$
begin
  if new.status = 'COMMISSION_PENDING' and old.status is distinct from 'COMMISSION_PENDING' then
    new.pending_since := now();
    new.overdue_flagged_at := null;
  end if;
  return new;
end $$;
drop trigger if exists ledger_pending_since on commission_ledger;
create trigger ledger_pending_since before update on commission_ledger for each row execute function ledger_track_pending();

create table if not exists commission_claim_events (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null default current_org() references organizations(id) on delete cascade,
  ledger_id   uuid not null references commission_ledger(id) on delete cascade,
  action      text not null check (action in ('claim_sent', 'acknowledged', 'disputed', 'overdue_flagged')),
  note        text check (length(note) <= 500),
  actor_id    uuid,
  created_at  timestamptz not null default clock_timestamp()
);
create index if not exists commission_claim_events_ledger_idx on commission_claim_events (ledger_id, created_at);

alter table commission_claim_events enable row level security;
grant select, insert on commission_claim_events to app_user;
drop policy if exists tenant_isolation on commission_claim_events;
create policy tenant_isolation on commission_claim_events for all to app_user using (org_id = current_org()) with check (org_id = current_org());

-- The reminder job's own notice kind.
alter table proactive_events drop constraint if exists proactive_events_kind_check;
alter table proactive_events add constraint proactive_events_kind_check check (kind in (
  'PRICE_DROP', 'PRICE_INCREASE', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
  'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH', 'PRICE_REQUEST', 'PROJECT_PRICE', 'PROJECT_VISIT', 'PORTAL_ALERT', 'PORTAL_LEAD',
  'COMMISSION_OVERDUE'));
