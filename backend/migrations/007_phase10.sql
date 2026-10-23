-- Payment terms, consultations, buyer objections and inbound developer announcements.

-- Payment terms: {"installments": 8, "interval_months": 3, "handover_pct": 10}. A unit's own terms win over its developer's.
alter table developers add column payment_plan jsonb;
alter table properties add column payment_plan jsonb;

-- A booking is either a physical site visit or a phone consultation (no stops, no route).
alter table tours_and_appointments
  add column kind text not null default 'SITE_VISIT' check (kind in ('SITE_VISIT', 'CONSULTATION')),
  add column dossier_sent_at timestamptz;

-- What a buyer hesitated about, kept for the broker's pre-tour dossier: [{"text": "...", "at": "2026-10-23T09:00:00Z"}]
alter table buyers_crm add column objections jsonb not null default '[]'::jsonb;

-- Each announcement email is processed once, however many times the sender's mail system delivers it.
create table inbound_announcements (
  message_id    text primary key,
  developer_id  uuid references developers(id) on delete set null,
  sender        text not null,
  subject       text,
  status        text not null check (status in ('PROCESSED', 'IGNORED', 'FAILED')),
  detail        jsonb not null default '{}'::jsonb,
  received_at   timestamptz not null default now()
);
alter table inbound_announcements enable row level security;
