"""Settings and services resolved per organization behind the names the rest of the code uses."""
import asyncio
import uuid

import pytest

from app.agent.prompts import persona_for
from app.config import get_settings
from app.runtime import TenantRuntime
from app.services.telegram import BotApiTelegramService, MockTelegramService
from app.tenancy import DEFAULT_ORG_ID
from app.vault import Vault, generate_master_key
from tests.agent_helpers import make_services


async def make_org(db_conn, label, **settings):
    cur = await db_conn.execute(
        "insert into organizations (slug, name) values (%s, %s) returning id", (f"rt-{label}-{uuid.uuid4().hex[:6]}", label.title()))
    org = (await cur.fetchone())["id"]
    await db_conn.execute("insert into org_settings (org_id, display_name) values (%s, %s)", (org, f"{label.title()} Realty"))
    for column, value in settings.items():
        await db_conn.execute(f"update org_settings set {column} = %s where org_id = %s", (value, org))
    return org


@pytest.fixture
def base_settings():
    return get_settings().model_copy(update={
        "broker_name": "The Platform Broker", "broker_telegram_chat_id": "111", "demo_fallback_telegram_chat_id": "999",
        "demo_fallback_email": "owner@example.com", "telegram_bot_token": "", "telegram_backend": "mock",
        "calendar_backend": "mock", "email_backend": "mock", "routing_backend": "mock",
    })


@pytest.fixture
def runtime_for(spy_repo, base_settings):
    def build(vault=True, **kw):
        base = make_services()
        return TenantRuntime(spy_repo, base_settings, base, Vault(generate_master_key()) if vault is True else vault, **kw), base
    return build


async def test_outside_any_scope_everything_is_the_original_configuration(runtime_for, base_settings):
    runtime, base = runtime_for()
    settings, services = runtime.tenant_settings(), runtime.proxied_services()
    assert settings.broker_name == "The Platform Broker" and settings.demo_fallback_telegram_chat_id == "999"
    await services.telegram.send_message(1, "hi")
    assert [m["text"] for m in base.telegram.messages] == ["hi"]


async def test_the_original_organization_keeps_the_platforms_channels_and_demo_redirects(runtime_for):
    runtime, base = runtime_for()
    settings, services = runtime.tenant_settings(), runtime.proxied_services()
    async with runtime.scope(DEFAULT_ORG_ID):
        assert settings.broker_telegram_chat_id == "111" and settings.demo_fallback_telegram_chat_id == "999"
        await services.telegram.send_message(1, "to the original agency")
    assert [m["text"] for m in base.telegram.messages] == ["to the original agency"]


async def test_a_new_agency_never_uses_the_platforms_bot_chat_or_demo_redirects(runtime_for, db_conn):
    org = await make_org(db_conn, "bole")
    runtime, base = runtime_for()
    settings, services = runtime.tenant_settings(), runtime.proxied_services()
    async with runtime.scope(org):
        assert settings.broker_name == "Bole Realty"
        assert settings.broker_telegram_chat_id == "" and settings.demo_fallback_telegram_chat_id == "" and settings.demo_fallback_email == ""
        await services.telegram.send_message(555, "must not reach the platform's bot")
    assert base.telegram.messages == [], "an agency without a bot sends nothing through anyone else's"


async def test_an_agencys_own_broker_name_chat_and_timezone_are_used(runtime_for, db_conn):
    org = await make_org(db_conn, "kazanchis", broker_name="Selam", broker_chat_id="4242", timezone="Africa/Nairobi")
    runtime, _ = runtime_for()
    settings = runtime.tenant_settings()
    async with runtime.scope(org):
        assert (settings.broker_name, settings.broker_telegram_chat_id, settings.timezone) == ("Selam", "4242", "Africa/Nairobi")
        assert settings.gemini_rpm == get_settings().gemini_rpm, "everything else passes straight through"


async def test_a_connected_bot_is_used_for_that_agency_only(runtime_for, db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    runtime, base = runtime_for()
    await runtime.store_secret(a, "telegram_bot_token", "123456:agency-a-token")
    services = runtime.proxied_services()
    async with runtime.scope(a):
        assert isinstance((await runtime.activate(a)).services.telegram, BotApiTelegramService)
    async with runtime.scope(b):
        assert isinstance((await runtime.activate(b)).services.telegram, MockTelegramService)
        await services.telegram.send_message(1, "b has no bot")
    assert base.telegram.messages == []


async def test_credentials_are_stored_encrypted_and_bound_to_their_organization(runtime_for, db_conn):
    a, b = await make_org(db_conn, "a"), await make_org(db_conn, "b")
    runtime, _ = runtime_for()
    await runtime.store_secret(a, "telegram_bot_token", "123456:plain-text-token")
    stored = (await (await db_conn.execute("select ciphertext from org_secrets where org_id = %s", (a,))).fetchone())["ciphertext"]
    assert "plain-text-token" not in stored
    assert (await runtime.secrets(a)) == {"telegram_bot_token": "123456:plain-text-token"}
    # the same stored value copied under another organization will not open there
    await runtime.store_secret(b, "google_service_account_json", "{}")
    await db_conn.execute("update org_secrets set ciphertext = %s where org_id = %s and name = 'google_service_account_json'", (stored, b))
    runtime.invalidate(b)
    assert await runtime.secrets(b) == {}


async def test_a_secret_that_cannot_be_opened_switches_that_agency_off_without_breaking_others(spy_repo, base_settings, db_conn):
    a = await make_org(db_conn, "a")
    writer = TenantRuntime(spy_repo, base_settings, make_services(), Vault(generate_master_key()))
    await writer.store_secret(a, "telegram_bot_token", "123456:token")
    wrong_key = TenantRuntime(spy_repo, base_settings, make_services(), Vault(generate_master_key()))
    assert await wrong_key.secrets(a) == {}
    assert isinstance((await wrong_key.activate(a)).services.telegram, MockTelegramService)


async def test_without_a_master_key_credentials_cannot_be_stored_and_channels_stay_off(runtime_for, db_conn):
    from app.vault import VaultError

    org = await make_org(db_conn, "nokey")
    runtime, _ = runtime_for(vault=None)
    with pytest.raises(VaultError):
        await runtime.store_secret(org, "telegram_bot_token", "x")
    assert await runtime.secrets(org) == {}


async def test_unknown_secret_names_are_refused(runtime_for, db_conn):
    org = await make_org(db_conn, "names")
    runtime, _ = runtime_for()
    with pytest.raises(ValueError):
        await runtime.store_secret(org, "admin_password", "x")


async def test_concurrent_work_for_different_agencies_never_sees_the_others_settings(runtime_for, db_conn):
    a, b = await make_org(db_conn, "a", broker_name="Alpha"), await make_org(db_conn, "b", broker_name="Beta")
    runtime, _ = runtime_for()
    settings = runtime.tenant_settings()

    async def read(org, expected):
        async with runtime.scope(org):
            seen = []
            for _ in range(5):
                await asyncio.sleep(0)
                seen.append(settings.broker_name)
            return seen == [expected] * 5

    assert all(await asyncio.gather(*(read(a, "Alpha") if i % 2 else read(b, "Beta") for i in range(10))))
    assert settings.broker_name == "The Platform Broker", "and nothing leaks out of the scopes"


async def test_work_started_inside_a_scope_keeps_it(runtime_for, db_conn):
    org = await make_org(db_conn, "tasks", broker_name="Taskers")
    runtime, _ = runtime_for()
    settings = runtime.tenant_settings()

    async def later():
        await asyncio.sleep(0)
        return settings.broker_name

    async with runtime.scope(org):
        task = asyncio.create_task(later())
    assert await task == "Taskers", "a background task started in the scope (a Telegram update) still works for that agency"


async def test_profiles_are_cached_until_they_expire_or_are_invalidated(runtime_for, db_conn):
    org = await make_org(db_conn, "cache", broker_name="Before")
    now = [0.0]
    runtime, _ = runtime_for(ttl=60, clock=lambda: now[0])
    settings = runtime.tenant_settings()
    async with runtime.scope(org):
        assert settings.broker_name == "Before"
    await db_conn.execute("update org_settings set broker_name = 'After' where org_id = %s", (org,))
    async with runtime.scope(org):
        assert settings.broker_name == "Before", "served from the cache"
    now[0] = 61
    async with runtime.scope(org):
        assert settings.broker_name == "After", "re-read after the time is up"
    await db_conn.execute("update org_settings set broker_name = 'Again' where org_id = %s", (org,))
    runtime.invalidate(org)
    async with runtime.scope(org):
        assert settings.broker_name == "Again", "and at once when told something changed"


async def test_the_agency_and_its_persona_reach_the_assistants_prompt(runtime_for, db_conn):
    org = await make_org(db_conn, "voice", broker_name="Meskel", persona="Always mention that viewings are free." + " x" * 400)
    runtime, _ = runtime_for()
    assert "You work for" not in persona_for("en")
    async with runtime.scope(org):
        prompt = persona_for("en")
        assert "You work for Meskel." in prompt and "viewings are free" in prompt
        assert len(prompt) < len(persona_for("en")) + 700, "the persona is bounded"
    assert "Meskel" not in persona_for("en")


async def test_settings_cannot_be_changed_through_the_wrapper(runtime_for):
    runtime, _ = runtime_for()
    with pytest.raises(AttributeError):
        runtime.tenant_settings().broker_name = "hacked"


async def test_an_unknown_organization_is_an_error_not_a_silent_default(runtime_for):
    runtime, _ = runtime_for()
    with pytest.raises(LookupError):
        async with runtime.scope(uuid.uuid4()):
            pass
