-- Projects can be found by meaning (an embedding), and a buyer can book a site visit to a project that has no priced unit.

alter table projects add column embedding vector(768);
create index projects_embedding_idx on projects using hnsw (embedding vector_cosine_ops);

-- A visit to a project is a normal visit with no unit stops; this says which project it was for.
alter table tours_and_appointments add column project_id uuid references projects(id) on delete set null;

alter table proactive_events drop constraint if exists proactive_events_kind_check;
alter table proactive_events add constraint proactive_events_kind_check check (kind in (
  'PRICE_DROP', 'PRICE_INCREASE', 'NEW_LISTING', 'TOUR_REMINDER', 'MILESTONE_REMINDER',
  'MILESTONE_OVERDUE', 'COMMISSION_READY', 'TAKEOVER_MATCH', 'PRICE_REQUEST', 'PROJECT_PRICE', 'PROJECT_VISIT'));
