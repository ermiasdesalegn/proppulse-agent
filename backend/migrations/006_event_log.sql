-- Durable activity events behind the dashboard's live stream. Every backend instance writes here and listens for
-- NOTIFY, so the stream works whichever instance a dashboard tab is connected to, and survives restarts.

create table event_log (
  id         bigint generated always as identity primary key,
  kind       text not null,
  data       jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index event_log_created_idx on event_log (created_at);

alter table event_log enable row level security;  -- no policies: only the backend (service role) reads or writes it

-- One row per Gemini request attempt: which node asked, which model answered, how long and how many tokens it took.
create table model_calls (
  id            bigint generated always as identity primary key,
  created_at    timestamptz not null default now(),
  operation     text not null,
  schema_name   text,
  lane          text not null,
  model         text,
  outcome       text not null,
  latency_ms    integer not null,
  attempt       integer not null default 1,
  fallbacks     text[] not null default '{}',
  input_tokens  integer,
  output_tokens integer,
  node          text,
  buyer_id      uuid,
  thread_id     text
);
create index model_calls_created_idx on model_calls (created_at desc);
create index model_calls_node_idx on model_calls (node, created_at desc);

alter table model_calls enable row level security;
