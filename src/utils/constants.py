"""Centralized constants for PageKeeper."""

# Bot device name used when syncing progress to ABS and KoSync
BOT_DEVICE_NAME = "pagekeeper-bot"

# Device names recognized as internal (not real user devices)
INTERNAL_DEVICE_NAMES = frozenset({"pagekeeper-bot", "pagekeeper"})

# Default ABS collection name for synced books
DEFAULT_COLLECTION_NAME = "pagekeeper"

# Default Booklore/Grimmory shelf name
DEFAULT_SHELF_NAME = "pagekeeper"
