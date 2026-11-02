-- Multi-tenancy, hardening: who did what inside an agency (invitations, roles, channels, settings).
--
-- Append-only for the agencies: `app_user` can read its own organization's entries and nothing else. Entries are written by the
-- platform path after the caller has been identified, so a tenant cannot add, edit or remove one. Updates are refused for everyone;
-- rows disappear only when their organization is deleted.

create table if not exists audit_log (
  id          bigint generated always as identity primary key,
  org_id      uuid not null references organizations(id) on delete cascade,
  actor_kind  text not null check (actor_kind in ('member', 'platform', 'system')),
  actor_id    uuid,
  action      text not null check (length(action) between 3 and 60),
  target      text,
  detail      jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists audit_log_org_idx on audit_log (org_id, id desc);

alter table audit_log enable row level security;
grant select on audit_log to app_user;
drop policy if exists own_org on audit_log;
create policy own_org on audit_log for select to app_user using (org_id = current_org());

create or replace function audit_log_no_update() returns trigger language plpgsql as $$
begin
  raise exception 'audit_log entries cannot be changed';
end $$;
drop trigger if exists audit_log_immutable on audit_log;
create trigger audit_log_immutable before update on audit_log for each row execute function audit_log_no_update();
