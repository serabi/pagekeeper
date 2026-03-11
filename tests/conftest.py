"""Shared pytest fixtures and module stubs for the test suite."""

import sys
from types import ModuleType

# Stub native modules only available inside Docker so that test files
# can import production code without raising ImportError.
for _mod_name in ('epubcfi',):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = ModuleType(_mod_name)
