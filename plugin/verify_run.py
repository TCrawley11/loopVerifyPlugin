"""Runs `lverify verify` in the session's sandbox, where the repo's toolchains are.

The plugin runs verify itself through Hermes' terminal tool and keeps the parsed report with the
review, so the gate never relies on a stamp the agent's sandbox wrote.
"""

import json
from dataclasses import dataclass
from typing import Any

DEFAULT_COMMAND = (
    "uvx -q --from git+https://github.com/TCrawley11/loopVerifyPlugin@main lverify verify --json"
)
# The terminal tool's foreground limit: a longer timeout turns the call into a background job.
TIMEOUT_SECONDS = 600


class VerifyError(Exception):
    """Verify could not produce a report; safe to show the user."""


@dataclass(frozen=True)
class VerifyRun:
    passed: bool
    sha: str
    clean: bool
    checks: tuple[dict[str, Any], ...]

    def to_record(self) -> dict[str, Any]:
        return {"passed": self.passed, "sha": self.sha, "clean": self.clean, "checks": self.checks}

    def describe(self) -> str:
        words = [f"{c['name']} {'pass' if c['passed'] else 'FAIL'}" for c in self.checks]
        return f"{'PASS' if self.passed else 'FAIL'} ({', '.join(words)})"


def run(ctx: Any, sandbox_dir: str, task_id: str | None, command: str) -> VerifyRun:
    args = {"command": command, "workdir": sandbox_dir, "timeout": TIMEOUT_SECONDS}
    raw = ctx.dispatch_tool("terminal", args, task_id=task_id)
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise VerifyError(f"the terminal tool returned no JSON: {str(raw)[:200]}") from exc
    output = str(result.get("output") or "")
    if result.get("exit_code") not in (0, 1):
        tail = "\n".join(output.splitlines()[-15:]) or str(result.get("error") or "")
        raise VerifyError(f"`{command}` exited with {result.get('exit_code')}:\n{tail}")
    return parse(output)


def parse(output: str) -> VerifyRun:
    """The report `lverify verify --json` printed, ignoring anything before it."""
    start = output.find("{")
    try:
        report, _ = json.JSONDecoder().raw_decode(output[start:]) if start >= 0 else (None, 0)
    except json.JSONDecodeError:
        report = None
    if not isinstance(report, dict) or "passed" not in report:
        raise VerifyError(f"verify printed no JSON report:\n{output[-800:]}")
    checks = tuple(
        {"name": r["name"], "passed": r["error"] is None and not r["new"], "new": len(r["new"])}
        for r in report.get("results", [])
    )
    return VerifyRun(bool(report["passed"]), str(report["sha"]), bool(report["clean"]), checks)
