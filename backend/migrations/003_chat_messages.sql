-- Queryable transcript for the broker console (the LangGraph checkpoint is not meant to be queried).

create table chat_messages (
  id               bigint generated always as identity primary key,
  buyer_id         uuid not null references buyers_crm(id) on delete cascade,
  role             text not null check (role in ('buyer', 'agent', 'broker')),
  content          text not null,
  source           text not null default 'text' check (source in ('text', 'voice', 'system')),
  telegram_file_id text,
  created_at       timestamptz not null default now()
);
create index chat_messages_buyer_idx on chat_messages (buyer_id, id);

alter table chat_messages enable row level security;

-- Lets the dashboard subscribe to new rows later; no policies exist, so nothing is readable by the public API.
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table chat_messages;
  end if;
end $$;
