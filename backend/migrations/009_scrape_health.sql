-- Phase 13: how a developer's page is read, and a record of runs that failed.

-- static: plain HTTP. browser: render JavaScript first. auto: plain HTTP, then the browser if no unit cards were found.
alter table developers add column fetch_mode text not null default 'auto' check (fetch_mode in ('static', 'browser', 'auto'));

alter table ingest_runs add column error text;
alter table ingest_runs add column items_found integer;
create index ingest_runs_developer_idx on ingest_runs (developer_id, id desc);
