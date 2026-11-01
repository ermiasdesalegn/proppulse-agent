-- Multi-tenancy, step 3b: no key and no link may cross an organization.
--
-- 1. A globally unique key on a private table makes two organizations collide (the same Telegram user at two agencies) and
--    lets one organization learn that a value exists in another. Each such key now includes the organization.
-- 2. A foreign key that names only `id` could point a row of organization A at a row of organization B. Each now carries
--    `org_id` too, so the database refuses it whatever the application does.

-- ---- per-organization keys ----
alter table buyers_crm drop constraint if exists buyers_crm_thread_id_key;
alter table buyers_crm drop constraint if exists buyers_crm_telegram_id_key;
alter table buyers_crm add constraint buyers_crm_org_thread_key   unique (org_id, thread_id);
alter table buyers_crm add constraint buyers_crm_org_telegram_key unique (org_id, telegram_id);

alter table proactive_events drop constraint if exists proactive_events_dedupe_key_key;
alter table proactive_events add constraint proactive_events_org_dedupe_key unique (org_id, dedupe_key);

drop index if exists channel_posts_once;
create unique index channel_posts_once on channel_posts (org_id, property_id, kind, price_etb) where kind <> 'MANUAL';

drop index if exists project_posts_once;
create unique index project_posts_once on project_posts (org_id, project_id, promo_key) where kind = 'PROMO';

alter table inbound_announcements drop constraint inbound_announcements_pkey;
alter table inbound_announcements add primary key (org_id, message_id);

alter table saved_homes drop constraint saved_homes_pkey;
alter table saved_homes add primary key (org_id, user_id, property_id);

alter table unit_layouts drop constraint unit_layouts_pkey;
alter table unit_layouts add primary key (org_id, property_id);

-- ---- links that cannot cross organizations ----
alter table commission_ledger add constraint commission_ledger_org_id_key unique (org_id, id);

do $$
declare
  fk record;
  links constant text[][] := array[
    -- child table, old constraint, child column, parent table, on delete clause
    ['tours_and_appointments', 'tours_and_appointments_buyer_id_fkey', 'buyer_id', 'buyers_crm',            'on delete cascade'],
    ['tour_properties',        'tour_properties_tour_id_fkey',         'tour_id',  'tours_and_appointments', 'on delete cascade'],
    ['commission_ledger',      'commission_ledger_buyer_id_fkey',      'buyer_id', 'buyers_crm',            ''],
    ['commission_ledger',      'commission_ledger_tour_id_fkey',       'tour_id',  'tours_and_appointments', 'on delete set null (tour_id)'],
    ['chat_messages',          'chat_messages_buyer_id_fkey',          'buyer_id', 'buyers_crm',            'on delete cascade'],
    ['proactive_events',       'proactive_events_buyer_id_fkey',       'buyer_id', 'buyers_crm',            'on delete cascade'],
    ['proactive_events',       'proactive_events_tour_id_fkey',        'tour_id',  'tours_and_appointments', 'on delete set null (tour_id)'],
    ['proactive_events',       'proactive_events_ledger_id_fkey',      'ledger_id','commission_ledger',     'on delete set null (ledger_id)'],
    ['lead_score_history',     'lead_score_history_buyer_id_fkey',     'buyer_id', 'buyers_crm',            'on delete cascade'],
    ['price_requests',         'price_requests_buyer_id_fkey',         'buyer_id', 'buyers_crm',            'on delete cascade']
  ];
  i int;
begin
  for i in 1 .. array_length(links, 1) loop
    execute format('alter table %I drop constraint %I', links[i][1], links[i][2]);
    execute format(
      'alter table %I add constraint %I foreign key (org_id, %I) references %I (org_id, id) %s',
      links[i][1], links[i][1] || '_' || links[i][3] || '_org_fk', links[i][3], links[i][4], links[i][5]);
  end loop;
end $$;
