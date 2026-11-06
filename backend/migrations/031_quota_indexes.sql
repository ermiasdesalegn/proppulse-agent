-- Quotas on money and on background jobs (docs/PLAN_REMAINING_SAAS.md, item 5).
--
-- Both are counted from an agency's own rows: the month's model calls, and the day's queued jobs. Each message and each enqueue asks, so
-- the questions have to be answered from an index on (agency, time) rather than by reading the whole table.

create index if not exists model_calls_org_idx on model_calls (org_id, created_at desc);
create index if not exists jobs_org_created_idx on jobs (org_id, created_at desc);
