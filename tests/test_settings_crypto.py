"""Tests for settings encryption: crypto primitives, key rotation, fail-closed."""

import pytest

from src.utils.settings_crypto import (
    ENVELOPE_PREFIX,
    SettingsCrypto,
    SettingsCryptoError,
    SettingsDecryptionError,
)

DUMMY_KEY = "dummy-master-secret-aaaaaaaaaaaaaaaaaaaa"
OTHER_KEY = "dummy-other-secret-bbbbbbbbbbbbbbbbbbbb"


class TestRoundTrip:
    def test_encrypt_then_decrypt_recovers_value(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        token = crypto.encrypt_value("HARDCOVER_TOKEN", "dummy-hardcover-token")

        assert token.startswith(ENVELOPE_PREFIX)
        assert "dummy-hardcover-token" not in token
        assert crypto.decrypt_value("HARDCOVER_TOKEN", token) == "dummy-hardcover-token"

    def test_none_passes_through(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        assert crypto.encrypt_value("HARDCOVER_TOKEN", None) is None
        assert crypto.decrypt_value("HARDCOVER_TOKEN", None) is None

    def test_empty_string_round_trips(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        token = crypto.encrypt_value("HARDCOVER_TOKEN", "")
        assert crypto.decrypt_value("HARDCOVER_TOKEN", token) == ""

    def test_is_encrypted_detection(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        token = crypto.encrypt_value("ABS_KEY", "dummy-abs-key")
        assert SettingsCrypto.is_encrypted(token)
        assert not SettingsCrypto.is_encrypted("dummy-abs-key")
        assert not SettingsCrypto.is_encrypted(None)


class TestIdempotence:
    def test_encrypt_does_not_double_encrypt(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        once = crypto.encrypt_value("KOSYNC_KEY", "dummy-kosync-key")
        twice = crypto.encrypt_value("KOSYNC_KEY", once)
        assert once == twice
        assert crypto.decrypt_value("KOSYNC_KEY", twice) == "dummy-kosync-key"

    def test_decrypt_passes_plaintext_through(self):
        """Plaintext (non-enveloped) values pass through during transition."""
        crypto = SettingsCrypto(DUMMY_KEY)
        assert crypto.decrypt_value("KOSYNC_KEY", "dummy-plaintext") == "dummy-plaintext"


class TestFailClosed:
    def test_wrong_key_raises(self):
        good = SettingsCrypto(DUMMY_KEY)
        bad = SettingsCrypto(OTHER_KEY)
        token = good.encrypt_value("ABS_KEY", "dummy-abs-key")
        with pytest.raises(SettingsDecryptionError):
            bad.decrypt_value("ABS_KEY", token)

    def test_tampered_token_raises(self):
        crypto = SettingsCrypto(DUMMY_KEY)
        token = crypto.encrypt_value("ABS_KEY", "dummy-abs-key")
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        with pytest.raises(SettingsDecryptionError):
            crypto.decrypt_value("ABS_KEY", tampered)

    def test_name_binding_mismatch_rejected(self):
        """A token encrypted for one key must not decrypt under another."""
        crypto = SettingsCrypto(DUMMY_KEY)
        token = crypto.encrypt_value("ABS_KEY", "dummy-abs-key")
        with pytest.raises(SettingsDecryptionError):
            crypto.decrypt_value("KOSYNC_KEY", token)

    def test_empty_master_secret_rejected(self):
        with pytest.raises(SettingsCryptoError):
            SettingsCrypto("")


class TestKeyRotation:
    def test_previous_key_still_decrypts(self):
        old = SettingsCrypto(OTHER_KEY)
        token = old.encrypt_value("HARDCOVER_TOKEN", "dummy-hardcover-token")

        rotated = SettingsCrypto(DUMMY_KEY, previous_secret=OTHER_KEY)
        assert rotated.decrypt_value("HARDCOVER_TOKEN", token) == "dummy-hardcover-token"

    def test_new_writes_use_primary_key(self):
        rotated = SettingsCrypto(DUMMY_KEY, previous_secret=OTHER_KEY)
        token = rotated.encrypt_value("HARDCOVER_TOKEN", "dummy-hardcover-token")

        primary_only = SettingsCrypto(DUMMY_KEY)
        assert primary_only.decrypt_value("HARDCOVER_TOKEN", token) == "dummy-hardcover-token"

    def test_without_previous_old_token_fails_closed(self):
        old = SettingsCrypto(OTHER_KEY)
        token = old.encrypt_value("HARDCOVER_TOKEN", "dummy-hardcover-token")
        current = SettingsCrypto(DUMMY_KEY)
        with pytest.raises(SettingsDecryptionError):
            current.decrypt_value("HARDCOVER_TOKEN", token)
