"""Credentials are encrypted per organization and cannot be moved between organizations or names."""
import uuid

import pytest

from app.vault import Vault, VaultError, generate_master_key

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def vault():
    return Vault(generate_master_key())


def test_a_secret_round_trips_and_is_not_readable_in_storage(vault):
    key, wrapped = vault.new_data_key(ORG_A)
    token = "123456:ABC-very-secret-bot-token"
    stored = vault.seal(key, ORG_A, "telegram_bot_token", token)
    assert token not in stored and stored.startswith("v1.")
    assert vault.open(vault.open_data_key(ORG_A, wrapped), ORG_A, "telegram_bot_token", stored) == token


def test_the_same_secret_encrypts_differently_each_time(vault):
    key, _ = vault.new_data_key(ORG_A)
    assert vault.seal(key, ORG_A, "telegram_bot_token", "same") != vault.seal(key, ORG_A, "telegram_bot_token", "same")


def test_a_stored_secret_does_not_open_under_another_name_or_organization(vault):
    key, _ = vault.new_data_key(ORG_A)
    stored = vault.seal(key, ORG_A, "telegram_bot_token", "secret")
    with pytest.raises(VaultError):
        vault.open(key, ORG_A, "google_service_account_json", stored)  # moved to another name
    with pytest.raises(VaultError):
        vault.open(key, ORG_B, "telegram_bot_token", stored)  # moved to another organization


def test_one_organizations_data_key_does_not_open_anothers_secrets(vault):
    key_a, _ = vault.new_data_key(ORG_A)
    key_b, _ = vault.new_data_key(ORG_B)
    stored = vault.seal(key_a, ORG_A, "telegram_bot_token", "secret")
    with pytest.raises(VaultError):
        vault.open(key_b, ORG_A, "telegram_bot_token", stored)


def test_a_wrapped_data_key_is_bound_to_its_organization_and_master_key(vault):
    _, wrapped = vault.new_data_key(ORG_A)
    with pytest.raises(VaultError):
        vault.open_data_key(ORG_B, wrapped)
    with pytest.raises(VaultError):
        Vault(generate_master_key()).open_data_key(ORG_A, wrapped)


@pytest.mark.parametrize("damaged", ["", "v1.", "v2.abcdef", "no-dot", "v1.!!!", "v1.AAAA"])
def test_damaged_values_are_refused_cleanly(vault, damaged):
    key, _ = vault.new_data_key(ORG_A)
    with pytest.raises(VaultError):
        vault.open(key, ORG_A, "telegram_bot_token", damaged)


def test_a_tampered_ciphertext_is_detected(vault):
    key, _ = vault.new_data_key(ORG_A)
    stored = vault.seal(key, ORG_A, "telegram_bot_token", "secret")
    flipped = stored[:-3] + ("AAA" if not stored.endswith("AAA") else "BBB")
    with pytest.raises(VaultError):
        vault.open(key, ORG_A, "telegram_bot_token", flipped)


@pytest.mark.parametrize("bad", ["", "short", "!!!not base64!!!", "QUJD"])
def test_a_bad_master_key_is_refused_with_a_clear_error(bad):
    with pytest.raises(VaultError):
        Vault(bad)


def test_generated_master_keys_are_valid_and_unique():
    a, b = generate_master_key(), generate_master_key()
    assert a != b
    Vault(a)
    Vault(b)
