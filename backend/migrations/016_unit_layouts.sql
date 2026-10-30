-- The room layout of a unit (rooms and their sizes in metres). The 3D model file and the picture are built from it on request,
-- so only this small JSON is stored. Saved from the dashboard; a buyer who asks for the details of the unit is sent the model.

create table unit_layouts (
  property_id  uuid primary key references properties(id) on delete cascade,
  layout       jsonb not null,
  updated_at   timestamptz not null default now()
);
alter table unit_layouts enable row level security;   -- only the backend (service role) reads it, like every other table
