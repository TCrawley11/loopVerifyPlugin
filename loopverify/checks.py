"""Runs one configured check and turns the tool's output into findings."""

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CheckSpec
from .ratchet import Finding

# Parsers whose findings are compared with the base. A failing exit-code check, such as a
# test run, is never excused by the base failing too.
DIFFERENTIAL_PARSERS = frozenset({"ruff", "mypy"})
OUTPUT_TAIL_LINES = 20


@dataclass(frozen=True)
class Run:
    """What one check produced. `error` means it produced no verdict at all."""

    findings: tuple[Finding, ...]
    error: str | None = None
    output_tail: str = ""


class _Unparsable(Exception):
    pass


def run(spec: CheckSpec, root: Path, cache_dir: Path) -> Run:
    cwd = root / spec.cwd
    env = {
        **os.environ,
        "RUFF_CACHE_DIR": str(cache_dir / "ruff"),
        "MYPY_CACHE_DIR": str(cache_dir / "mypy"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        done = subprocess.run(
            spec.command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Run((), error=f"timed out after {spec.timeout}s")
    except OSError as exc:
        return Run((), error=f"could not start {spec.command[0]!r} in {cwd}: {exc}")
    tail = "\n".join((done.stdout + done.stderr).splitlines()[-OUTPUT_TAIL_LINES:])
    try:
        findings = _PARSE[spec.parser](spec.name, done.returncode, done.stdout, cwd, root)
    except _Unparsable as exc:
        return Run((), error=str(exc), output_tail=tail)
    return Run(tuple(findings), output_tail=tail)


def _exit_code(check: str, code: int, stdout: str, cwd: Path, root: Path) -> list[Finding]:
    if code == 0:
        return []
    return [Finding(check, path="", code="exit-code", message=f"exited with {code}")]


def _ruff(check: str, code: int, stdout: str, cwd: Path, root: Path) -> list[Finding]:
    if code not in (0, 1):
        raise _Unparsable(f"ruff exited with {code}")
    try:
        items = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise _Unparsable(f"ruff output is not JSON: {exc}") from exc
    if not isinstance(items, list):
        raise _Unparsable("ruff output is not a JSON list")
    return [
        Finding(
            check,
            path=_relative(_field(item, "filename"), cwd, root),
            code=item.get("code") or "invalid-syntax",
            message=_field(item, "message"),
            line=int((item.get("location") or {}).get("row") or 0),
        )
        for item in items
    ]


def _mypy(check: str, code: int, stdout: str, cwd: Path, root: Path) -> list[Finding]:
    findings = []
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _Unparsable(f"mypy output line is not JSON: {exc}") from exc
        if not isinstance(item, dict) or item.get("severity") != "error":
            continue
        findings.append(
            Finding(
                check,
                path=_relative(_field(item, "file"), cwd, root),
                code=item.get("code") or "misc",
                message=_field(item, "message"),
                line=int(item.get("line") or 0),
            )
        )
    # mypy exits 2 both for blocking errors (reported as findings) and for crashes (none).
    if code not in (0, 1) and not (code == 2 and findings):
        raise _Unparsable(f"mypy exited with {code}")
    if code == 1 and not findings:
        raise _Unparsable("mypy reported errors but no JSON findings; is `-O json` set?")
    return findings


def _field(item: Any, name: str) -> str:
    value = item.get(name) if isinstance(item, dict) else None
    if not isinstance(value, str):
        raise _Unparsable(f"a finding lacks the string field {name!r}")
    return value


def _relative(path: str, cwd: Path, root: Path) -> str:
    """The path relative to the repo root, so HEAD and base runs report the same path."""
    absolute = (cwd / path).resolve()
    try:
        return absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path


_PARSE: dict[str, Callable[[str, int, str, Path, Path], list[Finding]]] = {
    "exit-code": _exit_code,
    "ruff": _ruff,
    "mypy": _mypy,
}
