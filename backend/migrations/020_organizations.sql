-- Multi-tenancy, step 1a: who the tenants are.
-- An organization is one agency (or one independent broker). Everything private will belong to exactly one.

create table if not exists organizations (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
  name        text not null check (length(btrim(name)) > 0),
  plan        text not null default 'free' check (plan in ('free', 'pro', 'business')),
  status      text not null default 'active' check (status in ('active', 'suspended')),
  created_at  timestamptz not null default now()
);

-- A user is a Supabase Auth account; the account itself lives in auth.users, so only its id is kept here.
create table if not exists memberships (
  org_id      uuid not null references organizations(id) on delete cascade,
  user_id     uuid not null,
  role        text not null check (role in ('owner', 'admin', 'agent', 'viewer')),
  created_at  timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index if not exists memberships_user_idx on memberships (user_id);

-- Every organization needs an owner at all times; the last owner can be neither removed nor demoted.
create or replace function memberships_keep_owner() returns trigger language plpgsql as $$
begin
  if (tg_op = 'DELETE' and old.role = 'owner')
     or (tg_op = 'UPDATE' and old.role = 'owner' and new.role <> 'owner') then
    -- Deleting the organization cascades here too; allow it when the organization is already gone.
    if exists (select 1 from organizations where id = old.org_id)
       and not exists (
         select 1 from memberships
          where org_id = old.org_id and role = 'owner' and user_id <> old.user_id
       ) then
      raise exception 'an organization must keep at least one owner';
    end if;
  end if;
  return coalesce(new, old);
end $$;

drop trigger if exists memberships_keep_owner_trg on memberships;
create trigger memberships_keep_owner_trg
  before update or delete on memberships
  for each row execute function memberships_keep_owner();

create table if not exists invitations (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references organizations(id) on delete cascade,
  email       text not null,
  role        text not null check (role in ('admin', 'agent', 'viewer')),   -- owners are made by transfer, not by invitation
  token_hash  text not null unique,                                          -- only a hash of the link token is stored
  invited_by  uuid,
  expires_at  timestamptz not null default now() + interval '7 days',
  accepted_at timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists invitations_org_idx on invitations (org_id);
-- One open invitation per address per organization.
create unique index if not exists invitations_open_idx
  on invitations (org_id, lower(email)) where accepted_at is null;

-- What used to be environment variables that described "the" broker.
create table if not exists org_settings (
  org_id            uuid primary key references organizations(id) on delete cascade,
  display_name      text,                 -- how the assistant introduces the agency
  persona           text,                 -- optional extra tone instructions for the assistant
  hero_property_id  uuid,                 -- the listing whose photo leads the public landing page
  broker_chat_id    text,                 -- where the agency's alerts go (Telegram chat)
  timezone          text not null default 'Africa/Addis_Ababa',
  brand_color       text check (brand_color is null or brand_color ~ '^#[0-9a-fA-F]{6}$'),
  logo_url          text,
  updated_at        timestamptz not null default now()
);

-- Tenant number one is the broker who runs PropPulse today. A fixed id lets later migrations and tests refer to it.
insert into organizations (id, slug, name, plan)
values ('00000000-0000-4000-8000-000000000001', 'proppulse', 'PropPulse', 'business')
on conflict (id) do nothing;

insert into org_settings (org_id, display_name)
values ('00000000-0000-4000-8000-000000000001', 'PropPulse')
on conflict (org_id) do nothing;

alter table organizations enable row level security;
alter table memberships   enable row level security;
alter table invitations   enable row level security;
alter table org_settings  enable row level security;
