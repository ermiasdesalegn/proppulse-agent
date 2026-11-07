-- Deal documents (docs/PLAN_PRODUCT_AND_MONEY.md, phase 1, item 2). Expand only.
--
-- The file itself is never in the database: it lives in a private storage bucket (or, in development, a local folder) under
-- <org_id>/<deal id>/<document id>.<ext>. This table is the index: what it is, how big, who added it and when. The key is built by
-- the server from the agency in scope, never from anything the browser sends, and files are only ever served through the API after the
-- caller's membership has been checked.

create table if not exists deal_documents (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null default current_org() references organizations(id) on delete cascade,
  ledger_id     uuid not null references commission_ledger(id) on delete cascade,
  kind          text not null check (kind in ('contract', 'receipt', 'acknowledgement', 'other')),
  filename      text not null check (length(filename) between 1 and 120),
  content_type  text not null check (content_type in ('application/pdf', 'image/png', 'image/jpeg', 'image/webp')),
  size_bytes    integer not null check (size_bytes between 1 and 10485760),
  storage_key   text not null,
  uploaded_by   uuid,
  created_at    timestamptz not null default now()
);
create index if not exists deal_documents_ledger_idx on deal_documents (ledger_id, created_at);

alter table deal_documents enable row level security;
grant select, insert, delete on deal_documents to app_user;
drop policy if exists tenant_isolation on deal_documents;
create policy tenant_isolation on deal_documents for all to app_user using (org_id = current_org()) with check (org_id = current_org());
