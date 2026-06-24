"""Repository for application settings (key-value store).

All values are stored and returned as strings (or None).
Callers are responsible for type conversion.

Secret settings (see ``SECRET_SETTING_KEYS``) are encrypted at rest:
encrypted on save and decrypted on read. Non-secret settings are stored
and returned verbatim.
"""

from sqlalchemy.dialects.sqlite import insert

from src.utils.secret_settings import is_secret_setting
from src.utils.settings_crypto import get_settings_crypto

from .base_repository import BaseRepository
from .models import Setting


class SettingsRepository(BaseRepository):
    @staticmethod
    def _persist_and_detach(session, obj):
        """Flush, refresh, and detach an object so it's usable outside the session."""
        session.flush()
        session.refresh(obj)
        session.expunge(obj)

    @staticmethod
    def _encrypt_for_storage(key, value):
        """Encrypt *value* if *key* is a secret setting; else return as-is."""
        if value is None or not is_secret_setting(key):
            return value
        return get_settings_crypto().encrypt_value(key, value)

    @staticmethod
    def _decrypt_from_storage(key, value):
        """Decrypt *value* if *key* is a secret setting; else return as-is."""
        if value is None or not is_secret_setting(key):
            return value
        return get_settings_crypto().decrypt_value(key, value)

    def get_setting(self, key, default=None):
        """Get a setting value by key. Returns a string or *default*."""
        with self.get_session() as session:
            setting = session.query(Setting).filter(Setting.key == key).first()
            if not setting:
                return default
            return self._decrypt_from_storage(key, setting.value)

    def set_setting(self, key, value):
        """Set a setting value. *value* is coerced to str (None stays None)."""
        with self.get_session() as session:
            str_value = str(value) if value is not None else None
            stored_value = self._encrypt_for_storage(key, str_value)
            stmt = (
                insert(Setting)
                .values(key=key, value=stored_value)
                .on_conflict_do_update(
                    index_elements=[Setting.key],
                    set_={"value": stored_value},
                )
            )
            session.execute(stmt)
            setting = session.query(Setting).filter(Setting.key == key).one()
            session.expunge(setting)
            return setting

    def get_all_settings(self):
        """Get all settings as a dictionary."""
        with self.get_session() as session:
            settings = session.query(Setting).all()
            return {s.key: self._decrypt_from_storage(s.key, s.value) for s in settings}

    def delete_setting(self, key):
        """Delete a setting by key."""
        return self._delete_one(Setting, Setting.key == key)

    def encrypt_plaintext_secrets(self):
        """Encrypt any plaintext secret values stored in the table.

        Idempotent: values already in the encrypted envelope format and
        empty/None values are skipped, so this is safe to run on every
        startup and never double-encrypts. Returns the number of values
        newly encrypted.
        """
        from src.utils.secret_settings import SECRET_SETTING_KEYS
        from src.utils.settings_crypto import SettingsCrypto

        crypto = get_settings_crypto()
        migrated = 0
        with self.get_session() as session:
            rows = session.query(Setting).filter(Setting.key.in_(SECRET_SETTING_KEYS)).all()
            for row in rows:
                if not row.value or SettingsCrypto.is_encrypted(row.value):
                    continue
                row.value = crypto.encrypt_value(row.key, row.value)
                migrated += 1
        return migrated
