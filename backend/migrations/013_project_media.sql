-- Pictures and videos for projects: what the scraper found is kept as image_url; what the broker adds is stored here, so a channel post always has something to show.

create table project_media (
  id         bigint generated always as identity primary key,
  project_id uuid not null references projects(id) on delete cascade,
  kind       text not null check (kind in ('photo', 'video')),
  mime       text not null,
  filename   text,
  size_bytes int not null,
  data       bytea not null,
  created_at timestamptz not null default now()
);
create index project_media_project_idx on project_media (project_id, id desc);
alter table project_media enable row level security;
