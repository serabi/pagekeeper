"""Tests for encrypt-on-save / decrypt-on-read in SettingsRepository and
the startup plaintext-to-encrypted migration."""

import os
import tempfile
from pathlib import Path

import pytest

from src.db.database_service import DatabaseService
from src.db.models import Setting
from src.utils.settings_crypto import (
    ENVELOPE_PREFIX,
    SettingsCrypto,
    SettingsDecryptionError,
    reset_settings_crypto,
)

DUMMY_KEY = "dummy-master-secret-aaaaaaaaaaaaaaaaaaaa"


@pytest.fixture()
def encryption_key(monkeypatch):
    monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", DUMMY_KEY)
    monkeypatch.delenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY_PREVIOUS", raising=False)
    reset_settings_crypto()
    yield DUMMY_KEY
    reset_settings_crypto()


@pytest.fixture()
def db_service():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield DatabaseService(str(Path(temp_dir) / "settings_enc.db"))


def _raw_value(db_service, key):
    """Read the on-disk (possibly encrypted) value, bypassing decryption."""
    with db_service.get_session() as session:
        row = session.query(Setting).filter(Setting.key == key).first()
        return row.value if row else None


class TestEncryptOnSaveDecryptOnRead:
    def test_secret_is_encrypted_at_rest(self, encryption_key, db_service):
        db_service.set_setting("HARDCOVER_TOKEN", "dummy-hardcover-token")

        stored = _raw_value(db_service, "HARDCOVER_TOKEN")
        assert SettingsCrypto.is_encrypted(stored)
        assert "dummy-hardcover-token" not in stored

        assert db_service.get_setting("HARDCOVER_TOKEN") == "dummy-hardcover-token"

    def test_get_all_settings_decrypts_secrets(self, encryption_key, db_service):
        db_service.set_setting("ABS_KEY", "dummy-abs-key")
        db_service.set_setting("ABS_SERVER", "https://abs.example")

        all_settings = db_service.get_all_settings()
        assert all_settings["ABS_KEY"] == "dummy-abs-key"
        assert all_settings["ABS_SERVER"] == "https://abs.example"

    def test_non_secret_stored_plaintext(self, encryption_key, db_service):
        db_service.set_setting("ABS_SERVER", "https://abs.example")
        assert _raw_value(db_service, "ABS_SERVER") == "https://abs.example"

    def test_none_value_not_encrypted(self, encryption_key, db_service):
        db_service.set_setting("HARDCOVER_TOKEN", None)
        assert _raw_value(db_service, "HARDCOVER_TOKEN") is None
        assert db_service.get_setting("HARDCOVER_TOKEN") is None

    def test_resave_does_not_double_encrypt(self, encryption_key, db_service):
        db_service.set_setting("KOSYNC_KEY", "dummy-kosync-key")
        # Round-trip the decrypted value back through set_setting.
        db_service.set_setting("KOSYNC_KEY", db_service.get_setting("KOSYNC_KEY"))
        second = _raw_value(db_service, "KOSYNC_KEY")
        # A single envelope prefix (not nested) and the value still decrypts.
        assert second.count(ENVELOPE_PREFIX) == 1
        assert db_service.get_setting("KOSYNC_KEY") == "dummy-kosync-key"


class TestMigration:
    def _seed_plaintext(self, db_service, key, value):
        """Write a plaintext value directly, simulating a pre-encryption DB."""
        with db_service.get_session() as session:
            session.add(Setting(key=key, value=value))

    def test_plaintext_secret_gets_encrypted(self, encryption_key, db_service):
        self._seed_plaintext(db_service, "HARDCOVER_TOKEN", "dummy-hardcover-token")

        migrated = db_service.encrypt_plaintext_secrets()

        assert migrated == 1
        assert SettingsCrypto.is_encrypted(_raw_value(db_service, "HARDCOVER_TOKEN"))
        assert db_service.get_setting("HARDCOVER_TOKEN") == "dummy-hardcover-token"

    def test_migration_is_idempotent(self, encryption_key, db_service):
        self._seed_plaintext(db_service, "ABS_KEY", "dummy-abs-key")

        assert db_service.encrypt_plaintext_secrets() == 1
        first = _raw_value(db_service, "ABS_KEY")
        assert db_service.encrypt_plaintext_secrets() == 0
        assert _raw_value(db_service, "ABS_KEY") == first

    def test_mixed_state_only_migrates_plaintext(self, encryption_key, db_service):
        self._seed_plaintext(db_service, "ABS_KEY", "dummy-abs-key")
        db_service.set_setting("KOSYNC_KEY", "dummy-kosync-key")  # already encrypted

        migrated = db_service.encrypt_plaintext_secrets()

        assert migrated == 1
        assert db_service.get_setting("ABS_KEY") == "dummy-abs-key"
        assert db_service.get_setting("KOSYNC_KEY") == "dummy-kosync-key"

    def test_empty_secret_not_migrated(self, encryption_key, db_service):
        self._seed_plaintext(db_service, "ABS_KEY", "")
        assert db_service.encrypt_plaintext_secrets() == 0
        assert not SettingsCrypto.is_encrypted(_raw_value(db_service, "ABS_KEY") or "")

    def test_non_secret_not_migrated(self, encryption_key, db_service):
        self._seed_plaintext(db_service, "ABS_SERVER", "https://abs.example")
        assert db_service.encrypt_plaintext_secrets() == 0
        assert _raw_value(db_service, "ABS_SERVER") == "https://abs.example"


class TestWrongKeyFailsClosed:
    def test_read_with_wrong_key_raises(self, encryption_key, db_service, monkeypatch):
        db_service.set_setting("HARDCOVER_TOKEN", "dummy-hardcover-token")

        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", "dummy-wrong-secret-cccccccccccccc")
        reset_settings_crypto()

        with pytest.raises(SettingsDecryptionError):
            db_service.get_setting("HARDCOVER_TOKEN")

    def test_get_all_isolates_undecryptable_secret(self, encryption_key, db_service, monkeypatch):
        # An undecryptable secret must not drop the other settings.
        db_service.set_setting("HARDCOVER_TOKEN", "dummy-hardcover-token")
        db_service.set_setting("ABS_KEY", "dummy-abs-key")
        db_service.set_setting("ABS_SERVER", "https://abs.example")  # non-secret

        # Corrupt only one secret's ciphertext, leaving the rest valid.
        with db_service.get_session() as session:
            row = session.query(Setting).filter(Setting.key == "HARDCOVER_TOKEN").one()
            row.value = row.value + "tampered"

        all_settings = db_service.get_all_settings()

        assert "HARDCOVER_TOKEN" not in all_settings  # failed closed, omitted
        assert all_settings["ABS_KEY"] == "dummy-abs-key"  # healthy secret still loads
        assert all_settings["ABS_SERVER"] == "https://abs.example"  # non-secret unaffected

        # Targeted reads still fail closed for the bad key.
        with pytest.raises(SettingsDecryptionError):
            db_service.get_setting("HARDCOVER_TOKEN")


class TestKeyRotationThroughRepository:
    def test_previous_key_decrypts_after_rotation(self, db_service, monkeypatch):
        old_key = "dummy-old-secret-dddddddddddddddddddd"
        new_key = "dummy-new-secret-eeeeeeeeeeeeeeeeeeee"

        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", old_key)
        monkeypatch.delenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY_PREVIOUS", raising=False)
        reset_settings_crypto()
        db_service.set_setting("HARDCOVER_TOKEN", "dummy-hardcover-token")

        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", new_key)
        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY_PREVIOUS", old_key)
        reset_settings_crypto()

        assert db_service.get_setting("HARDCOVER_TOKEN") == "dummy-hardcover-token"
        reset_settings_crypto()


class TestMasterSecretDiscovery:
    def test_explicit_env_var_preferred(self, monkeypatch):
        from src.app_runtime import get_settings_master_secret

        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", "dummy-explicit-key")
        assert get_settings_master_secret() == "dummy-explicit-key"

    def test_falls_back_to_flask_secret(self, monkeypatch, tmp_path):
        from src import app_runtime

        monkeypatch.delenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
        monkeypatch.setenv("DATA_DIR", str(tmp_path))

        secret = app_runtime.get_settings_master_secret()
        assert secret
        # Stable across calls — persisted to the data dir.
        assert app_runtime.get_settings_master_secret() == secret

    def test_previous_secret_read_from_env(self, monkeypatch):
        from src.app_runtime import get_settings_previous_secret

        monkeypatch.setenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY_PREVIOUS", "dummy-prev-key")
        assert get_settings_previous_secret() == "dummy-prev-key"

        monkeypatch.delenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY_PREVIOUS", raising=False)
        assert get_settings_previous_secret() == ""

    def test_raises_when_no_key_and_data_dir_unwritable(self, monkeypatch, tmp_path):
        """An unwritable data dir with no key env vars must fail closed.

        Returning an ephemeral key here would let the startup migration
        encrypt secrets with a key that vanishes on the next restart,
        permanently stranding them.
        """
        from src.app_runtime import EphemeralSecretKeyError, get_settings_master_secret

        monkeypatch.delenv("PAGEKEEPER_SETTINGS_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

        unwritable = tmp_path / "readonly"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        monkeypatch.setenv("DATA_DIR", str(unwritable / "data"))

        try:
            with pytest.raises(EphemeralSecretKeyError):
                get_settings_master_secret()
        finally:
            unwritable.chmod(0o700)

    def test_flask_session_secret_still_falls_back_to_ephemeral(self, monkeypatch, tmp_path):
        """The Flask session secret keeps the lenient ephemeral fallback."""
        from src.app_runtime import get_or_create_secret_key

        unwritable = tmp_path / "readonly"
        unwritable.mkdir()
        unwritable.chmod(0o500)
        monkeypatch.setenv("DATA_DIR", str(unwritable / "data"))

        try:
            assert get_or_create_secret_key()  # no raise: ephemeral is acceptable here
        finally:
            unwritable.chmod(0o700)


class TestEnvSyncAfterEncryption:
    """The settings save/load cycle must expose decrypted secrets to env."""

    def test_load_settings_syncs_decrypted_secret_to_env(self, encryption_key, db_service, monkeypatch):
        from src.utils.config_loader import load_settings

        monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)

        # Mimic the settings route: save then sync to os.environ.
        db_service.set_setting("HARDCOVER_TOKEN", "dummy-hardcover-token")
        assert SettingsCrypto.is_encrypted(_raw_value(db_service, "HARDCOVER_TOKEN"))

        load_settings(db_service)
        assert os.environ["HARDCOVER_TOKEN"] == "dummy-hardcover-token"

    def test_startup_migration_then_env_sync(self, encryption_key, db_service, monkeypatch):
        from src.utils.config_loader import ConfigLoader

        monkeypatch.delenv("ABS_KEY", raising=False)

        # Seed a legacy plaintext secret directly.
        with db_service.get_session() as session:
            session.add(Setting(key="ABS_KEY", value="dummy-abs-key"))

        ConfigLoader.migrate_secrets_encryption(db_service)
        assert SettingsCrypto.is_encrypted(_raw_value(db_service, "ABS_KEY"))

        ConfigLoader.load_settings(db_service)
        assert os.environ["ABS_KEY"] == "dummy-abs-key"
