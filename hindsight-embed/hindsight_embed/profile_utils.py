"""
Shared utilities for profile handling across hindsight-embed and hindsight-all.

This module provides the canonical implementation for profile name sanitization
and database URL generation to ensure consistency across all Hindsight packages.
"""

import re


def sanitize_profile_name(profile: str | None) -> str:
    """
    Sanitize a profile name for use in database names and file paths.

    Replaces any character that is not alphanumeric, dash, or underscore with a dash.
    This ensures the profile name is safe for use in PostgreSQL database names,
    file system paths, and URLs.

    Args:
        profile: Profile name to sanitize, or None for default

    Returns:
        Sanitized profile name (defaults to "default" if None)

    Examples:
        >>> sanitize_profile_name("my-app")
        'my-app'
        >>> sanitize_profile_name("My App!")
        'My-App-'
        >>> sanitize_profile_name(None)
        'default'
    """
    if profile is None:
        return "default"
    return re.sub(r"[^a-zA-Z0-9_-]", "-", profile)


def get_profile_database_url(profile: str | None, db_url: str | None = None) -> str:
    """
    Get the database URL for a given profile.

    If a custom database URL is provided, it's returned as-is.
    Otherwise, generates a profile-specific pg0 database URL.

    Args:
        profile: Profile name (will be sanitized)
        db_url: Optional custom database URL. If "pg0" or None, generates profile-specific URL.

    Returns:
        Database URL for the profile

    Examples:
        >>> get_profile_database_url("myapp")
        'pg0://hindsight-embed-myapp'
        >>> get_profile_database_url("My App!")
        'pg0://hindsight-embed-My-App-'
        >>> get_profile_database_url("myapp", "postgresql://custom")
        'postgresql://custom'
        >>> get_profile_database_url("myapp", "pg0")
        'pg0://hindsight-embed-myapp'
    """
    # If custom database URL provided and not the default "pg0", use it
    if db_url and db_url != "pg0":
        return db_url

    # Generate profile-specific pg0 database URL
    safe_profile = sanitize_profile_name(profile)
    return f"pg0://hindsight-embed-{safe_profile}"
