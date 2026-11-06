-- A job queue that is fair between agencies (docs/PLAN_NEXT_SAAS.md, feature 1).
--
-- The timed work (proactive run, reverse-RAG alerts, reminders, promotion ads) used to run inside the cron request, one agency
-- after another. It is now queued as one job per agency and run by workers inside the app (app/jobs.py), which claim jobs with
-- `for update skip locked`, take agencies in turn (`org_last_served`) and hold a lease while they work.
--
-- Platform-owned: `app_user` gets no grant on either table, so an agency cannot read, add or change a job, not even its own. The
-- API shows an agency its jobs through the platform path after checking who is asking.

create table if not exists jobs (
  id            bigint generated always as identity primary key,
  org_id        uuid not null references organizations(id) on delete cascade,
  kind          text not null check (kind ~ '^[a-z][a-z0-9_.]{2,59}$'),
  payload       jsonb not null default '{}'::jsonb,
  dedupe_key    text not null check (length(dedupe_key) between 1 and 200),
  status        text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed', 'dead')),
  priority      smallint not null default 0,
  run_after     timestamptz not null default now(),
  attempts      integer not null default 0 check (attempts >= 0),
  max_attempts  integer not null default 5 check (max_attempts between 1 and 50),
  locked_by     text,
  locked_until  timestamptz,
  last_error    text,
  result        jsonb,
  created_at    timestamptz not null default now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  unique (org_id, kind, dedupe_key)  -- the same job for the same agency is queued once
);

-- What the workers look for: work that is due ('failed' is a job waiting for its retry), agency by agency, most urgent and oldest first.
create index if not exists jobs_due_idx on jobs (status, run_after) where status in ('queued', 'failed');
create index if not exists jobs_org_due_idx on jobs (org_id, priority desc, id) where status in ('queued', 'failed');
-- The per-agency running limit and the reaper.
create index if not exists jobs_running_idx on jobs (org_id) where status = 'running';
create index if not exists jobs_lease_idx on jobs (locked_until) where status = 'running';
-- The console's list, newest first, per agency.
create index if not exists jobs_org_idx on jobs (org_id, id desc);

-- When each agency was last given a worker. The claim takes the agency served longest ago first: round-robin.
create table if not exists org_last_served (
  org_id     uuid primary key references organizations(id) on delete cascade,
  served_at  timestamptz not null
);

revoke all on jobs, org_last_served from app_user;
-- Row-level security on, with no policy: no role but the owner (the platform) sees a row, even if a grant is added by mistake.
alter table jobs enable row level security;
alter table org_last_served enable row level security;
