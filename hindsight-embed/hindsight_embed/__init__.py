"""Hindsight embedded CLI - local memory operations without a server."""

from .profile_utils import get_profile_database_url, sanitize_profile_name

__version__ = "0.4.8"

__all__ = [
    "get_profile_database_url",
    "sanitize_profile_name",
]
