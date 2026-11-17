"""A project's verify configuration: `config.toml` in its state repo folder.

base = "origin/perception/perception_dev"

[[check]]
name = "lint"
parser = "ruff"
cwd = "src/perception"
command = ["uvx", "ruff@0.17.5", "check", "--output-format", "json", "."]
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import SetupError

PARSERS = ("ruff", "mypy", "exit-code", "debt")
DEBT_KEYS = frozenset({"comment_max_lines", "expiry_max_days"})
CHECK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
DEFAULT_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class DebtSettings:
    comment_max_lines: int = 3
    expiry_max_days: int = 90


@dataclass(frozen=True)
class CheckSpec:
    """One check. A `debt` check is built in: it has settings instead of a command."""

    name: str
    parser: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    debt: DebtSettings | None = None


@dataclass(frozen=True)
class Config:
    base: str
    checks: tuple[CheckSpec, ...]


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SetupError(f"no verify config at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SetupError(f"{path} is not valid TOML: {exc}") from exc
    return parse(raw, str(path))


def parse(raw: dict[str, Any], where: str) -> Config:
    base = raw.get("base")
    if not isinstance(base, str) or not base:
        raise SetupError(f"{where}: `base` must name the branch changes are compared against")
    checks = raw.get("check")
    if not isinstance(checks, list) or not checks:
        raise SetupError(f"{where}: define at least one [[check]]")
    specs = tuple(_check(item, f"{where}: check {index + 1}") for index, item in enumerate(checks))
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names):
        raise SetupError(f"{where}: check names must be unique, got {names}")
    return Config(base=base, checks=specs)


def _check(raw: Any, where: str) -> CheckSpec:
    if not isinstance(raw, dict):
        raise SetupError(f"{where}: must be a table")
    allowed = {"name", "parser", "cwd"} | (
        DEBT_KEYS if raw.get("parser") == "debt" else {"command", "timeout"}
    )
    unknown = set(raw) - allowed
    if unknown:
        raise SetupError(f"{where}: unknown keys {sorted(unknown)}")
    name, parser, command = raw.get("name"), raw.get("parser"), raw.get("command", [])
    cwd, timeout = raw.get("cwd", "."), raw.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(name, str) or not CHECK_NAME.fullmatch(name):
        raise SetupError(f"{where}: `name` must be letters, digits, `-` or `_`, like `lint`")
    if parser not in PARSERS:
        raise SetupError(f"{where}: `parser` must be one of {', '.join(PARSERS)}")
    debt = _debt(raw, where) if parser == "debt" else None
    if debt is None and not (
        isinstance(command, list) and command and all(isinstance(a, str) for a in command)
    ):
        raise SetupError(f"{where}: `command` must be a non-empty list of strings")
    if not isinstance(cwd, str) or not cwd or not _inside_repo(cwd):
        raise SetupError(f"{where}: `cwd` must be a relative path inside the repo, like `src`")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise SetupError(f"{where}: `timeout` must be a positive number of seconds")
    cwd = PurePosixPath(cwd).as_posix()
    return CheckSpec(
        name=name, parser=parser, command=tuple(command), cwd=cwd, timeout=timeout, debt=debt
    )


def _debt(raw: dict[str, Any], where: str) -> DebtSettings:
    defaults = DebtSettings()
    values = {key: raw.get(key, getattr(defaults, key)) for key in DEBT_KEYS}
    for key, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SetupError(f"{where}: `{key}` must be a positive whole number")
    return DebtSettings(**values)


def _inside_repo(path: str) -> bool:
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts
