"""Master-key rotation (app/vault.py, app/keys.py): data keys name their master key, a rotation re-seals only them, it survives a crash,
and the server refuses to start when a key it needs was removed too early. Real database; the race test uses its own connections."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.jobs import REGISTRY, JobQueue, JobWorker
from app.keys import REWRAP, check_keys, key_status, rewrap_org, start_rotation
from app.runtime import TenantRuntime
from app.vault import UnknownKey, Vault, VaultError, _parse, generate_master_key, key_id, stored_key_id
from tests.agent_helpers import make_services
from tests.test_channels_api import bearer, world  # noqa: F401  (world is a fixture)

OLD, NEW = generate_master_key(), generate_master_key()
ORG = uuid.uuid4()


def v1_wrapped(master: str, org, data_key: bytes) -> str:
    """A data key as the vault stored it before key ids existed."""
    return Vault._seal(AESGCM(_parse(master)), data_key, f"key:{org}")


# ---------------- the format ----------------


def test_a_v2_value_names_its_key_and_a_v1_value_still_opens():
    old = Vault(OLD)
    key, wrapped = old.new_data_key(ORG)
    assert wrapped.startswith(f"v2.{key_id(OLD)}.") and stored_key_id(wrapped) == key_id(OLD)
    legacy = v1_wrapped(OLD, ORG, key)
    assert stored_key_id(legacy) is None
    both = Vault([NEW, OLD])
    assert both.open_data_key(ORG, legacy) == key == both.open_data_key(ORG, wrapped)
    assert both.opening_key_id(ORG, legacy) == key_id(OLD), "a v1 value belongs to the key that opens it"
    assert both.current_key_id == key_id(NEW) and both.key_ids == [key_id(NEW), key_id(OLD)]


def test_the_newest_key_seals_and_rewrapping_keeps_the_same_data_key():
    both = Vault([NEW, OLD])
    key, old_wrapped = Vault(OLD).new_data_key(ORG)
    fresh = both.rewrap(ORG, old_wrapped)
    assert stored_key_id(fresh) == key_id(NEW) and Vault(NEW).open_data_key(ORG, fresh) == key
    secret = Vault(OLD).seal(key, ORG, "telegram_bot_token", "123:abc")
    assert Vault(NEW).open(Vault(NEW).open_data_key(ORG, fresh), ORG, "telegram_bot_token", secret) == "123:abc", "the secret itself never changed"


def test_the_bindings_still_hold_and_a_missing_key_is_named():
    both = Vault([NEW, OLD])
    key, wrapped = both.new_data_key(ORG)
    secret = both.seal(key, ORG, "telegram_bot_token", "123:abc")
    with pytest.raises(VaultError):
        both.open_data_key(uuid.uuid4(), wrapped)  # another agency's row
    with pytest.raises(VaultError):
        both.open(key, ORG, "google_service_account_json", secret)  # another secret's name
    with pytest.raises(UnknownKey) as missing:
        Vault(OLD).open_data_key(ORG, wrapped)
    assert missing.value.key_id == key_id(NEW)
    with pytest.raises(VaultError):
        Vault(NEW).open_data_key(ORG, v1_wrapped(OLD, ORG, key))
    assert key_id(OLD) not in OLD and len(key_id(OLD)) == 8, "the id is a fingerprint, not part of the key"


def test_settings_list_the_keys_newest_first_and_keep_the_old_single_one():
    s = get_settings().model_copy(update={"secrets_master_keys": f"{NEW}, {OLD}", "secrets_master_key": OLD})
    assert s.master_key_list == [NEW, OLD]
    assert get_settings().model_copy(update={"secrets_master_keys": NEW, "secrets_master_key": OLD}).master_key_list == [NEW, OLD]
    assert get_settings().model_copy(update={"secrets_master_keys": "", "secrets_master_key": OLD}).master_key_list == [OLD]


# ---------------- a rotation on the database ----------------


@pytest_asyncio.fixture
async def agencies(spy_repo, db_conn):
    """30 agencies with a bot token and a calendar key each, sealed under the old master key (a third of them in the old v1 format)."""
    await db_conn.execute("delete from org_keys")
    base = get_settings()
    old_runtime = TenantRuntime(spy_repo, base, make_services(), Vault(OLD))
    orgs = []
    for i in range(30):
        cur = await db_conn.execute("insert into organizations (slug, name) values (%s, %s) returning id", (f"rot-{i}-{uuid.uuid4().hex[:6]}", f"Rotation {i}"))
        org = (await cur.fetchone())["id"]
        await old_runtime.store_secret(org, "telegram_bot_token", f"{1000 + i}:token-{i}")
        await old_runtime.store_secret(org, "google_service_account_json", f'{{"client_email": "x@y", "private_key": "k{i}"}}')
        if i % 3 == 0:
            key = Vault(OLD).open_data_key(org, await spy_repo.as_platform().org_key(org))
            await db_conn.execute("update org_keys set wrapped_key = %s where org_id = %s", (v1_wrapped(OLD, org, key), org))
        orgs.append(org)
    return orgs


async def snapshot(db_conn):
    keys = {r["org_id"]: r["wrapped_key"] for r in await (await db_conn.execute("select org_id, wrapped_key from org_keys")).fetchall()}
    secrets = {(r["org_id"], r["name"]): r["ciphertext"] for r in await (await db_conn.execute("select org_id, name, ciphertext from org_secrets")).fetchall()}
    return keys, secrets


def rotating(spy_repo, *masters):
    runtime = TenantRuntime(spy_repo, get_settings(), make_services(), Vault(list(masters)))
    return runtime, SimpleNamespace(runtime=runtime, repo=spy_repo)


async def all_open(runtime, orgs):
    return all([len(await runtime.secrets(org)) == 2 for org in orgs])


async def test_a_rotation_reseals_every_data_key_and_no_secret(spy_repo, db_conn, agencies):
    keys_before, secrets_before = await snapshot(db_conn)
    runtime, state = rotating(spy_repo, NEW, OLD)
    queue = JobQueue(spy_repo.pool)
    queued = await start_rotation(spy_repo.pool, runtime.vault, queue)
    assert len(queued) == 30 and all(q.kind == REWRAP for q in queued)
    assert await JobWorker(queue, state).run_until_idle() == 30

    keys_after, secrets_after = await snapshot(db_conn)
    assert {stored_key_id(keys_after[o]) for o in agencies} == {key_id(NEW)}, "no data key is left on the old master key"
    assert secrets_after == secrets_before, "the secrets' own ciphertexts did not change"
    assert all(keys_after[o] != keys_before[o] for o in agencies)
    new_only, _ = rotating(spy_repo, NEW)
    assert await all_open(new_only, agencies), "every secret opens with the new key alone"
    assert (await check_keys(spy_repo.pool, new_only.vault))["done"]
    trail = await (await db_conn.execute("select count(*) as n from audit_log where action = 'key.rewrap' and actor_kind = 'system' and org_id = any(%s)", (agencies,))).fetchone()
    assert trail["n"] == 30

    again = await rewrap_org(spy_repo.pool, runtime.vault, agencies[0])
    assert again["status"] == "already current", "a second run changes nothing"
    assert await start_rotation(spy_repo.pool, runtime.vault, queue) == []


async def test_a_rotation_cut_off_halfway_keeps_every_agency_working_and_a_second_run_finishes_only_the_rest(spy_repo, db_conn, agencies):
    runtime, state = rotating(spy_repo, NEW, OLD)
    queue = JobQueue(spy_repo.pool)
    await start_rotation(spy_repo.pool, runtime.vault, queue)
    worker = JobWorker(queue, state)
    for _ in range(15):
        await worker.run_one()  # then the process dies

    status = await key_status(spy_repo.pool, runtime.vault)
    assert status["by_key"] == {key_id(NEW): 15, key_id(OLD): 15} and not status["done"]
    assert await all_open(runtime, agencies), "with both keys loaded, every agency still works"
    rest = await start_rotation(spy_repo.pool, runtime.vault, queue)
    assert len(rest) == 15 and not any(q.created for q in rest), "only the agencies still behind, and their jobs are the ones already queued"
    assert await JobWorker(queue, state).run_until_idle() == 15
    assert (await key_status(spy_repo.pool, runtime.vault))["done"]


async def test_a_rewrap_that_fails_its_check_keeps_the_old_key(spy_repo, db_conn, agencies, monkeypatch):
    runtime, _ = rotating(spy_repo, NEW, OLD)
    before, _ = await snapshot(db_conn)

    def broken(*a, **k):
        raise VaultError("the check failed")

    monkeypatch.setattr(runtime.vault, "open", broken)
    with pytest.raises(VaultError):
        await rewrap_org(spy_repo.pool, runtime.vault, agencies[1])
    after, _ = await snapshot(db_conn)
    assert after[agencies[1]] == before[agencies[1]], "nothing was written"


async def test_removing_the_old_key_too_early_stops_the_server_and_says_which_key(spy_repo, db_conn, agencies):
    runtime, _ = rotating(spy_repo, NEW, OLD)
    for org in agencies[:-1]:
        await rewrap_org(spy_repo.pool, runtime.vault, org)
    with pytest.raises(RuntimeError) as stop:
        await check_keys(spy_repo.pool, Vault(NEW))
    assert "1 agency data keys cannot be opened" in str(stop.value)
    assert ("a v1 key" if (len(agencies) - 1) % 3 == 0 else key_id(OLD)) in str(stop.value)
    assert (await check_keys(spy_repo.pool, Vault([NEW, OLD])))["unknown"] == 0, "with the old key back it starts"
    with pytest.raises(UnknownKey):
        await start_rotation(spy_repo.pool, Vault(NEW), JobQueue(spy_repo.pool))


def test_the_rewrap_job_is_registered():
    assert REWRAP in REGISTRY


# ---------------- a secret stored while its agency is being re-sealed ----------------


async def test_a_bot_connected_during_its_agencys_rewrap_ends_up_working_under_the_new_key():
    url = get_settings().database_url
    setup = await AsyncConnection.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=None)
    org = (await (await setup.execute("insert into organizations (slug, name) values (%s, 'Race') returning id", (f"rot-race-{uuid.uuid4().hex[:6]}",))).fetchone())["id"]
    pool = AsyncConnectionPool(url, min_size=1, max_size=4, open=False, kwargs={"row_factory": dict_row, "prepare_threshold": None})
    await pool.open(wait=True)
    try:
        from app.repository import Repository

        repo = Repository(pool)
        old = TenantRuntime(repo, get_settings(), make_services(), Vault(OLD))
        await old.store_secret(org, "telegram_bot_token", "111:first")
        both = TenantRuntime(repo, get_settings(), make_services(), Vault([NEW, OLD]))

        holder = await AsyncConnection.connect(url, row_factory=dict_row, prepare_threshold=None)
        await holder.execute("select 1 from org_keys where org_id = %s for update", (org,))  # someone else holds the key row
        rewrap = asyncio.create_task(rewrap_org(pool, both.vault, org))
        await asyncio.sleep(0.2)
        assert not rewrap.done(), "the re-wrap waits for the row"
        await both.store_secret(org, "google_service_account_json", '{"client_email": "a@b", "private_key": "k"}')  # the bot is connected now
        await holder.rollback()
        await holder.close()
        assert (await asyncio.wait_for(rewrap, 10))["status"] == "rewrapped"

        new_only = TenantRuntime(repo, get_settings(), make_services(), Vault(NEW))
        assert await new_only.secrets(org) == {"telegram_bot_token": "111:first", "google_service_account_json": '{"client_email": "a@b", "private_key": "k"}'}
        wrapped = (await (await setup.execute("select wrapped_key from org_keys where org_id = %s", (org,))).fetchone())["wrapped_key"]
        assert stored_key_id(wrapped) == key_id(NEW)
    finally:
        await setup.execute("delete from organizations where id = %s", (org,))
        await setup.close()
        await pool.close()


# ---------------- the operator's view ----------------


async def test_only_the_operator_sees_the_key_status_and_it_holds_ids_only(world, db_conn):
    from tests.test_channels_api import agency

    admin = uuid.uuid4()
    a, _ = await agency(db_conn, "keys", (admin, "admin"))
    assert (await world.client.get("/api/keys/status", headers={**bearer(admin), "X-Org-Id": str(a)})).status_code == 403
    r = await world.client.get("/api/keys/status", headers={"X-Admin-Key": "test-admin-key"})
    assert r.status_code == 200 and r.json()["configured"] and r.json()["current"] == world.runtime.vault.current_key_id
    assert len(r.json()["current"]) == 8 and "private" not in r.text

