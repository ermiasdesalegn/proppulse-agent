-- Slice A of docs/PLAN_NEXT_SLICES.md: buyer account basics. Expand only; both columns are new and every existing row keeps today's behaviour.
--
-- buyers_crm.alerts_enabled        true = the buyer receives price-drop and new-listing alerts (the default, so nothing changes until a buyer switches it off)
-- tours_and_appointments.cancelled_at   set when a buyer cancels a booking; a cancelled booking is no longer reminded or counted as upcoming

alter table buyers_crm add column if not exists alerts_enabled boolean not null default true;
alter table tours_and_appointments add column if not exists cancelled_at timestamptz;
