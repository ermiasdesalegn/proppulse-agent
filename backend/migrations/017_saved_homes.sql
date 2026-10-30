-- Homes a signed-in buyer has saved on the website. user_id is the buyer's Supabase Auth id (it is not a foreign key: auth lives in Supabase's own schema).

create table saved_homes (
  user_id     uuid not null,
  property_id uuid not null references properties(id) on delete cascade,
  created_at  timestamptz not null default now(),
  primary key (user_id, property_id)
);
create index saved_homes_user_idx on saved_homes (user_id, created_at desc);
alter table saved_homes enable row level security;   -- only the backend (service role) reads it, like every other table
