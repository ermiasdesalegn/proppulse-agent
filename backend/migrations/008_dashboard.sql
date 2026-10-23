-- Phase 12: explainable lead scores and the reset-demo bookkeeping.

-- The signals a buyer has earned, so the score can be explained: {"budget": {"points": 20, "label": "Stated a budget"}, ...}
alter table buyers_crm add column score_breakdown jsonb not null default '{}'::jsonb;

-- One row each time a buyer's score goes up, with what raised it.
create table lead_score_history (
  id         bigint generated always as identity primary key,
  buyer_id   uuid not null references buyers_crm(id) on delete cascade,
  score      integer not null check (score between 0 and 100),
  gained     jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index lead_score_history_buyer_idx on lead_score_history (buyer_id, id);
alter table lead_score_history enable row level security;
