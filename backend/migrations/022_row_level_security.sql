-- Multi-tenancy, step 3: the database itself keeps organizations apart.
--
-- The application switches to the `app_user` role for the length of each transaction (SET LOCAL ROLE) and labels the
-- transaction with its organization (`app.org_id`, step 2). `app_user` does not own the tables and cannot bypass row-level
-- security, so the policies below apply to it. A transaction with no label sees no rows and can write none: a bug fails closed.
--
-- Table owners (migrations, seed scripts, operators) are deliberately NOT forced through the policies; they are the platform.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'app_user') then
    create role app_user nologin nobypassrls;
  end if;
end $$;

-- The connection the pool logs in with must be allowed to SET ROLE to it.
do $$ begin execute format('grant app_user to %I', current_user); end $$;

create or replace function current_org() returns uuid
  language sql stable
  as $$ select nullif(current_setting('app.org_id', true), '')::uuid $$;

grant usage on schema public to app_user;
do $$ begin
  if exists (select 1 from pg_namespace where nspname = 'extensions') then
    execute 'grant usage on schema extensions to app_user';  -- where Supabase keeps pgvector
  end if;
end $$;
grant usage, select on all sequences in schema public to app_user;

do $$
declare
  t text;
  tenant_one constant uuid := '00000000-0000-4000-8000-000000000001';
  private_tables constant text[] := array[
    'buyers_crm', 'chat_messages', 'tours_and_appointments', 'tour_properties', 'commission_ledger',
    'lead_score_history', 'saved_homes', 'proactive_events', 'price_requests', 'channel_posts',
    'project_posts', 'inbound_announcements', 'unit_layouts', 'model_calls', 'event_log'
  ];
  shared_tables constant text[] := array[
    'developers', 'projects', 'project_media', 'price_history', 'crawl_runs', 'ingest_runs'
  ];
begin
  foreach t in array private_tables loop
    execute format('alter table %I enable row level security', t);
    execute format('grant select, insert, update, delete on %I to app_user', t);
    execute format('drop policy if exists tenant_isolation on %I', t);
    execute format(
      'create policy tenant_isolation on %I for all to app_user using (org_id = current_org()) with check (org_id = current_org())', t);
    -- A write that names no organization takes the caller's. Outside a labelled transaction (the platform's own scripts,
    -- which bypass the policies anyway) it falls back to tenant one until every writer names its organization.
    execute format('alter table %I alter column org_id set default coalesce(current_org(), %L::uuid)', t, tenant_one);
  end loop;

  -- Shared market data: every organization reads it. Writing it from a tenant's transaction is still allowed for now
  -- (the crawlers and the dashboard share one code path); a later step moves those writes to a separate platform role.
  foreach t in array shared_tables loop
    execute format('alter table %I enable row level security', t);
    execute format('grant select, insert, update, delete on %I to app_user', t);
    execute format('drop policy if exists shared_data on %I', t);
    execute format('create policy shared_data on %I for all to app_user using (true) with check (true)', t);
  end loop;
end $$;

-- Listings: the shared market catalogue (org_id null) plus the organization's own exclusive ones.
alter table properties enable row level security;
grant select, insert, update, delete on properties to app_user;
drop policy if exists market_or_own on properties;
create policy market_or_own on properties for all to app_user
  using (org_id is null or org_id = current_org())
  with check (org_id is null or org_id = current_org());

-- The tenancy tables themselves: a member can read their own organization; changes go through the platform for now.
grant select on organizations to app_user;
drop policy if exists own_org on organizations;
create policy own_org on organizations for select to app_user using (id = current_org());

grant select on memberships to app_user;
drop policy if exists own_org on memberships;
create policy own_org on memberships for select to app_user using (org_id = current_org());

grant select, update on org_settings to app_user;
drop policy if exists own_org on org_settings;
create policy own_org on org_settings for all to app_user using (org_id = current_org()) with check (org_id = current_org());

grant select, insert, update, delete on invitations to app_user;
drop policy if exists own_org on invitations;
create policy own_org on invitations for all to app_user using (org_id = current_org()) with check (org_id = current_org());
