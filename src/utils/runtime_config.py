"""Lightweight environment-backed config helpers for runtime code."""

import os


def get_str(key, default=""):
    return os.environ.get(key, default)


def get_bool(key, default=False):
    raw_default = "true" if default else "false"
    return os.environ.get(key, raw_default).strip().lower() in ("true", "1", "yes", "on")


def get_int(key, default):
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return int(default)


def get_float(key, default):
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return float(default)
