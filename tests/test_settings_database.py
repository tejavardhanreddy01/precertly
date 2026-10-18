"""The database URL is built from PRECERTLY_DATABASE_URL, or from separate PRECERTLY_DB_* parts."""

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from precertly.settings import DatabaseSettingsError, Settings

DB_VARS = (
    "DATABASE_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
)
NASTY_PASSWORD = "p@ss:w/rd%2F#?x y"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever CI or a shell exports must not leak into these tests."""
    for name in DB_VARS:
        monkeypatch.delenv(f"PRECERTLY_{name}", raising=False)


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(f"PRECERTLY_{name}", value)
    return Settings(_env_file=None)


PARTS = {
    "DB_HOST": "db.internal",
    "DB_NAME": "precertly",
    "DB_USER": "app",
    "DB_PASSWORD": "s3cret",
}


def test_default_is_the_local_compose_database(monkeypatch: pytest.MonkeyPatch) -> None:
    url = settings(monkeypatch).sqlalchemy_url()
    assert url.render_as_string(hide_password=False) == (
        "postgresql+asyncpg://precertly:precertly@localhost:5433/precertly"
    )


def test_url_wins_over_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    url = settings(
        monkeypatch,
        DATABASE_URL="postgresql+asyncpg://u:pw@urlhost:6543/urldb",
        DB_HOST="parthost",  # would be incomplete on its own; the URL still wins
    ).sqlalchemy_url()
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "urlhost",
        6543,
        "urldb",
        "u",
        "pw",
    )


def test_blank_url_does_not_win(monkeypatch: pytest.MonkeyPatch) -> None:
    url = settings(monkeypatch, DATABASE_URL="  ", **PARTS).sqlalchemy_url()
    assert url.host == "db.internal"


def test_parts_build_an_asyncpg_url_and_port_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    url = settings(monkeypatch, **PARTS).sqlalchemy_url()
    assert url.drivername == "postgresql+asyncpg"
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "db.internal",
        5432,
        "precertly",
        "app",
        "s3cret",
    )


def test_port_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings(monkeypatch, DB_PORT="6432", **PARTS).sqlalchemy_url().port == 6432


@pytest.mark.parametrize("present", list(PARTS) + ["DB_PORT"])
def test_any_part_makes_the_others_required(monkeypatch: pytest.MonkeyPatch, present: str) -> None:
    value = "6432" if present == "DB_PORT" else PARTS[present]
    config = settings(monkeypatch, **{present: value})
    with pytest.raises(DatabaseSettingsError) as error:
        config.sqlalchemy_url()
    message = str(error.value)
    for name in PARTS:
        assert (name in message) == (name != present)
    assert value not in message or present == "DB_PORT"


def test_missing_parts_are_all_named(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(monkeypatch, DB_HOST="h")
    with pytest.raises(DatabaseSettingsError) as error:
        config.sqlalchemy_url()
    for name in ("DB_NAME", "DB_USER", "DB_PASSWORD"):
        assert f"PRECERTLY_{name}" in str(error.value)


def test_blank_part_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(monkeypatch, **{**PARTS, "DB_PASSWORD": ""})
    with pytest.raises(DatabaseSettingsError, match="PRECERTLY_DB_PASSWORD"):
        config.sqlalchemy_url()


def test_special_characters_in_the_password_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    url = settings(monkeypatch, **{**PARTS, "DB_PASSWORD": NASTY_PASSWORD}).sqlalchemy_url()
    assert url.password == NASTY_PASSWORD
    rendered = url.render_as_string(hide_password=False)
    assert NASTY_PASSWORD not in rendered  # it was escaped, not pasted in
    assert make_url(rendered).password == NASTY_PASSWORD
    assert (make_url(rendered).host, make_url(rendered).database) == ("db.internal", "precertly")
    # The engine sees the same password the caller gave.
    assert create_async_engine(url).url.password == NASTY_PASSWORD


def test_password_stays_out_of_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(monkeypatch, **{**PARTS, "DB_PASSWORD": NASTY_PASSWORD})
    url = config.sqlalchemy_url()
    for text in (repr(config), str(config), repr(url), str(url), repr(create_async_engine(url))):
        assert NASTY_PASSWORD not in text
        assert "s3cret" not in text


def test_url_password_stays_out_of_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(monkeypatch, DATABASE_URL="postgresql+asyncpg://u:hunter2@h/d")
    assert "hunter2" not in repr(config)
    assert "hunter2" not in str(config.sqlalchemy_url())


def test_invalid_url_error_does_not_echo_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings(monkeypatch, DATABASE_URL="not-a-url hunter2")
    with pytest.raises(DatabaseSettingsError) as error:
        config.sqlalchemy_url()
    assert "hunter2" not in str(error.value)
    assert "hunter2" not in repr(error.value)
