-- Multi-tenancy, step 1b: every private table learns which organization owns each row.
-- "Expand" phase: the column defaults to tenant number one, so the running app keeps working untouched.
-- Later steps add the policies and drop the default once every write path names its organization.

do $$
declare
  t text;
  tenant_one constant uuid := '00000000-0000-4000-8000-000000000001';
  private_tables constant text[] := array[
    'buyers_crm', 'chat_messages', 'tours_and_appointments', 'tour_properties', 'commission_ledger',
    'lead_score_history', 'saved_homes', 'proactive_events', 'price_requests', 'channel_posts',
    'project_posts', 'inbound_announcements', 'unit_layouts', 'model_calls', 'event_log'
  ];
begin
  foreach t in array private_tables loop
    execute format('alter table %I add column if not exists org_id uuid', t);
    execute format('update %I set org_id = %L where org_id is null', t, tenant_one);
    execute format('alter table %I alter column org_id set default %L', t, tenant_one);
    execute format('alter table %I alter column org_id set not null', t);
    begin
      execute format('alter table %I add constraint %I foreign key (org_id) references organizations(id)', t, t || '_org_fk');
    exception when duplicate_object then null;
    end;
    execute format('create index if not exists %I on %I (org_id)', t || '_org_idx', t);
  end loop;
end $$;

-- Listings are shared market data unless an agency owns one exclusively: NULL means "everyone can see it".
alter table properties add column if not exists org_id uuid references organizations(id);
create index if not exists properties_org_idx on properties (org_id) where org_id is not null;

-- Targets for the composite foreign keys that come next: they let the database refuse a row in one organization that
-- points at a row in another (a tour for organization A can never reference a buyer of organization B).
alter table buyers_crm             add constraint buyers_crm_org_id_key             unique (org_id, id);
alter table tours_and_appointments add constraint tours_and_appointments_org_id_key unique (org_id, id);
