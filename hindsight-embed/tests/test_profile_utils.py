"""Tests for shared profile utilities."""

from hindsight_embed.profile_utils import get_profile_database_url, sanitize_profile_name


def test_sanitize_profile_name_default():
    """Test sanitization with None returns default."""
    assert sanitize_profile_name(None) == "default"


def test_sanitize_profile_name_simple():
    """Test sanitization with simple alphanumeric names."""
    assert sanitize_profile_name("myapp") == "myapp"
    assert sanitize_profile_name("my-app") == "my-app"
    assert sanitize_profile_name("my_app") == "my_app"
    assert sanitize_profile_name("app123") == "app123"


def test_sanitize_profile_name_special_chars():
    """Test sanitization replaces special characters with dashes."""
    assert sanitize_profile_name("my app") == "my-app"
    assert sanitize_profile_name("my.app") == "my-app"
    assert sanitize_profile_name("my@app!") == "my-app-"
    assert sanitize_profile_name("My App 2.0!") == "My-App-2-0-"


def test_get_profile_database_url_default():
    """Test database URL generation with default pg0."""
    assert get_profile_database_url("myapp") == "pg0://hindsight-embed-myapp"
    assert get_profile_database_url("myapp", None) == "pg0://hindsight-embed-myapp"
    assert get_profile_database_url("myapp", "pg0") == "pg0://hindsight-embed-myapp"


def test_get_profile_database_url_sanitization():
    """Test database URL generation sanitizes profile names."""
    assert get_profile_database_url("My App!") == "pg0://hindsight-embed-My-App-"
    assert get_profile_database_url(None) == "pg0://hindsight-embed-default"


def test_get_profile_database_url_custom():
    """Test database URL generation with custom database."""
    custom_url = "postgresql://user:pass@localhost/db"
    assert get_profile_database_url("myapp", custom_url) == custom_url
    assert get_profile_database_url("any-profile", custom_url) == custom_url


def test_consistency_between_functions():
    """Test that using functions together produces consistent results."""
    profile = "My App 2.0"
    safe_name = sanitize_profile_name(profile)
    db_url = get_profile_database_url(profile)

    # Should match the pattern
    assert db_url == f"pg0://hindsight-embed-{safe_name}"
    assert db_url == "pg0://hindsight-embed-My-App-2-0"
