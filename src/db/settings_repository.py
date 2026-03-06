"""Repository for application settings (key-value store)."""

from .base_repository import BaseRepository
from .models import Setting


class SettingsRepository(BaseRepository):

    def get_setting(self, key, default=None):
        """Get a setting value by key."""
        with self.get_session() as session:
            setting = session.query(Setting).filter(Setting.key == key).first()
            return setting.value if setting else default

    def set_setting(self, key, value):
        """Set a setting value."""
        with self.get_session() as session:
            existing = session.query(Setting).filter(Setting.key == key).first()
            if existing:
                existing.value = str(value) if value is not None else None
                session.flush()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            else:
                new_setting = Setting(key=key, value=str(value) if value is not None else None)
                session.add(new_setting)
                session.flush()
                session.refresh(new_setting)
                session.expunge(new_setting)
                return new_setting

    def get_all_settings(self):
        """Get all settings as a dictionary."""
        with self.get_session() as session:
            settings = session.query(Setting).all()
            return {s.key: s.value for s in settings}

    def delete_setting(self, key):
        """Delete a setting by key."""
        return self._delete_one(Setting, Setting.key == key)
