-- Multi-tenancy, hardening: an organization's role can read the shared market data but no longer write it.
--
-- Until now `app_user` could insert, update and delete in the shared tables because the crawlers and the dashboard went through
-- the same code path as a tenant's work. Those writes now run as the platform (the connection's own role, see
-- Repository._shared), so a bug or a stolen tenant session cannot rewrite prices, developers or projects for every agency.

do $$
declare
  t text;
  shared_tables constant text[] := array[
    'developers', 'projects', 'project_media', 'price_history', 'crawl_runs', 'ingest_runs', 'properties'
  ];
begin
  foreach t in array shared_tables loop
    execute format('revoke insert, update, delete, truncate on %I from app_user', t);
    execute format('grant select on %I to app_user', t);
  end loop;
end $$;

-- The policies only decide which rows are visible now; the shared ones need no write side any more.
drop policy if exists shared_data on developers;
drop policy if exists shared_data on projects;
drop policy if exists shared_data on project_media;
drop policy if exists shared_data on price_history;
drop policy if exists shared_data on crawl_runs;
drop policy if exists shared_data on ingest_runs;
create policy shared_data on developers     for select to app_user using (true);
create policy shared_data on projects       for select to app_user using (true);
create policy shared_data on project_media  for select to app_user using (true);
create policy shared_data on price_history  for select to app_user using (true);
create policy shared_data on crawl_runs     for select to app_user using (true);
create policy shared_data on ingest_runs    for select to app_user using (true);

drop policy if exists market_or_own on properties;
create policy market_or_own on properties for select to app_user using (org_id is null or org_id = current_org());
