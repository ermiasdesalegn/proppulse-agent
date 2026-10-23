-- The broker is told when a developer raises a price, not only when one drops.
alter table proactive_events drop constraint if exists proactive_events_kind_check;
alter table proactive_events add constraint proactive_events_kind_check check (kind in (
  'PRICE_DROP', 'PRICE_INCREASE', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
  'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH'));
