-- Portal crawls started from the dashboard or by schedule: one row per portal per run, so the broker sees history and alerts have a source.
-- Units read from a portal remember the address of the original listing (the broker contacts that agent; buyers are never sent there).

alter table properties add column if not exists source_url text;

create table crawl_runs (
  id            bigint generated always as identity primary key,
  portal        text not null,
  request_id    text,                       -- set when the dashboard asked for the run; the runner claims its queued row with it
  trigger       text not null default 'schedule' check (trigger in ('dashboard', 'schedule', 'manual')),
  status        text not null default 'QUEUED' check (status in ('QUEUED', 'RUNNING', 'FINISHED', 'BLOCKED', 'FAILED')),
  dry_run       boolean not null default true,
  mode          text not null default 'incremental',   -- 'full', 'incremental' or 'limited'
  page_limit    int,
  run_url       text,                       -- the GitHub Actions page of the run, for the raw logs
  stats         jsonb not null default '{}'::jsonb,
  blocked_reason text,
  error         text,
  requested_at  timestamptz not null default now(),
  started_at    timestamptz,
  finished_at   timestamptz
);
alter table crawl_runs enable row level security;   -- only the backend (service role) reads it, like every other table
create index crawl_runs_recent_idx on crawl_runs (requested_at desc);
create index crawl_runs_request_idx on crawl_runs (request_id, portal);

alter table proactive_events drop constraint if exists proactive_events_kind_check;
alter table proactive_events add constraint proactive_events_kind_check check (kind in (
  'PRICE_DROP', 'PRICE_INCREASE', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
  'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH', 'PRICE_REQUEST', 'PROJECT_PRICE', 'PROJECT_VISIT', 'PORTAL_ALERT', 'PORTAL_LEAD'));
