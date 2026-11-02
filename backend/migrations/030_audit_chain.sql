-- A tamper-evident audit trail (docs/PLAN_REMAINING_SAAS.md, item 4).
--
-- Every entry of an agency carries the hash of the entry before it and a hash of its own fields, so deleting, editing or reordering an
-- entry (which the table's owner can still do) breaks the chain at that point. `seq` numbers an agency's entries in the order they were
-- chained; it is not `id`, because two inserts can take their ids in one order and reach the lock in the other. The chain only proves that
-- something changed. What makes a deletion recoverable is the export (app/auditchain.py), which copies entries to storage the database
-- cannot reach and remembers how far it got in `audit_export_state`.

alter table audit_log add column if not exists seq bigint;
alter table audit_log add column if not exists prev_hash text;
alter table audit_log add column if not exists hash text;

create or replace function audit_entry_hash(
  p_prev text, p_seq bigint, p_org uuid, p_kind text, p_actor uuid, p_action text, p_target text, p_detail jsonb, p_at timestamptz
) returns text language sql immutable as $$
  select encode(sha256(convert_to(jsonb_build_array(
    p_prev, p_seq, p_org, p_kind, p_actor, p_action, p_target, p_detail, to_char(p_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
  )::text, 'UTF8')), 'hex')
$$;

-- Entries written before this migration are chained in the order they were made.
do $$
declare
  r record;
  cur_org uuid;
  n bigint;
  prev text;
  h text;
begin
  alter table audit_log disable trigger audit_log_immutable;
  for r in select * from audit_log where hash is null order by org_id, id loop
    if cur_org is distinct from r.org_id then
      cur_org := r.org_id; n := 0; prev := '';
    end if;
    n := n + 1;
    h := audit_entry_hash(prev, n, r.org_id, r.actor_kind, r.actor_id, r.action, r.target, r.detail, r.created_at);
    update audit_log set seq = n, prev_hash = prev, hash = h where id = r.id;
    prev := h;
  end loop;
  alter table audit_log enable trigger audit_log_immutable;
end $$;

alter table audit_log alter column seq set not null;
alter table audit_log alter column prev_hash set not null;
alter table audit_log alter column hash set not null;
create unique index if not exists audit_log_chain_idx on audit_log (org_id, seq);

-- One writer at a time per agency, so the chain has no forks: the lock is held to the end of the inserting transaction.
create or replace function audit_chain() returns trigger language plpgsql as $$
declare
  last_seq  bigint;
  last_hash text;
begin
  perform pg_advisory_xact_lock(hashtextextended('audit:' || new.org_id::text, 0));
  select seq, hash into last_seq, last_hash from audit_log where org_id = new.org_id order by seq desc limit 1;
  new.seq := coalesce(last_seq, 0) + 1;
  new.prev_hash := coalesce(last_hash, '');
  new.hash := audit_entry_hash(new.prev_hash, new.seq, new.org_id, new.actor_kind, new.actor_id, new.action, new.target, new.detail, new.created_at);
  return new;
end $$;
drop trigger if exists audit_log_chain on audit_log;
create trigger audit_log_chain before insert on audit_log for each row execute function audit_chain();

-- How far each agency's trail has been copied out. Platform only: no grant, no policy.
create table if not exists audit_export_state (
  org_id      uuid primary key references organizations(id) on delete cascade,
  last_seq    bigint not null default 0,
  last_hash   text not null default '',
  exported_at timestamptz not null default now()
);
alter table audit_export_state enable row level security;
