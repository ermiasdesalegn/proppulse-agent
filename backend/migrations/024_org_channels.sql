-- Multi-tenancy, step 5: each organization brings its own channels (its Telegram bot, its calendar).
--
-- Credentials are never stored as readable text. Each organization has its own data key, itself encrypted with a master key that
-- lives only in the server's environment (SECRETS_MASTER_KEY); each secret is encrypted with the data key and bound to its
-- organization and name, so a ciphertext copied to another row will not open. Neither table is granted to `app_user`: the
-- application reads them only through the platform path, after checking who is asking.

create table if not exists org_keys (
  org_id       uuid primary key references organizations(id) on delete cascade,
  wrapped_key  text not null,
  created_at   timestamptz not null default now()
);

create table if not exists org_secrets (
  org_id      uuid not null references organizations(id) on delete cascade,
  name        text not null check (name in ('telegram_bot_token', 'telegram_webhook_secret', 'google_service_account_json')),
  ciphertext  text not null,
  updated_at  timestamptz not null default now(),
  primary key (org_id, name)
);

alter table org_keys    enable row level security;
alter table org_secrets enable row level security;
-- no policies and no grants for app_user: with row-level security on and no policy, the restricted role sees nothing at all.

-- Non-secret channel details, shown in the console and used to introduce the agency.
alter table org_settings add column if not exists broker_name text;
alter table org_settings add column if not exists google_calendar_id text;
alter table org_settings add column if not exists telegram_bot_username text;
alter table org_settings add column if not exists telegram_connected_at timestamptz;
