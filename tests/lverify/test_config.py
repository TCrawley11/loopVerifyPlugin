import pytest

from loopverify import config
from loopverify.errors import SetupError

CHECK = {"name": "lint", "parser": "ruff", "command": ["ruff", "check"]}


def parse(**overrides):
    raw = {"base": "origin/main", "check": [CHECK]}
    raw.update(overrides)
    return config.parse(raw, "config.toml")


def test_a_minimal_config_gets_defaults():
    (spec,) = parse().checks
    assert spec == config.CheckSpec("lint", "ruff", ("ruff", "check"), ".", 1800)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base": ""}, "`base`"),
        ({"check": []}, "at least one"),
        ({"check": [CHECK, CHECK]}, "unique"),
        ({"check": [{**CHECK, "name": "../escape"}]}, "`name`"),
        ({"check": [{**CHECK, "name": "lint/perception"}]}, "`name`"),
        ({"check": [{**CHECK, "name": ".hidden"}]}, "`name`"),
        ({"check": ["lint"]}, "must be a table"),
        ({"check": [{**CHECK, "parser": "pylint"}]}, "`parser`"),
        ({"check": [{**CHECK, "command": "ruff check"}]}, "`command`"),
        ({"check": [{**CHECK, "command": []}]}, "`command`"),
        ({"check": [{**CHECK, "cwd": "../other"}]}, "`cwd`"),
        ({"check": [{**CHECK, "cwd": "/abs"}]}, "`cwd`"),
        ({"check": [{**CHECK, "timeout": 0}]}, "`timeout`"),
        ({"check": [{**CHECK, "timeout": True}]}, "`timeout`"),
        ({"check": [{**CHECK, "shell": True}]}, "unknown keys"),
    ],
)
def test_an_invalid_config_names_the_problem(overrides, message):
    with pytest.raises(SetupError, match=message):
        parse(**overrides)


def test_load_reports_a_missing_or_broken_file(tmp_path):
    with pytest.raises(SetupError, match="no verify config"):
        config.load(tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("base = ")
    with pytest.raises(SetupError, match="not valid TOML"):
        config.load(tmp_path / "config.toml")
