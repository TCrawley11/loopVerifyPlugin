"""P0 probes: evidence that Hermes does what PLAN.md assumes.

Temporary. Delete this module and skills/p0-probe once docs/p0-report.md lists every
assumption as proven on the host.
"""

import html
import json
import time
import webbrowser
from pathlib import Path
from typing import Any

from . import gate

LOCAL_SURFACES = frozenset({"cli", "tui", "desktop", "local"})


class EvidenceLog:
    """Append-only JSON lines file of what each probe observed."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, probe: str, **facts: Any) -> None:
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "probe": probe, **facts}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line]


def make_gate_observer(ctx: Any, log: EvidenceLog):
    """A post_tool_call observer that records how Hermes handled each `gh pr create`."""

    def observe_gate(
        tool_name: str = "", args: Any = None, status: str = "", error_message: str = "", **_: Any
    ) -> None:
        if gate.opens_pr(tool_name, args):
            log.record(
                "gate",
                status=status,
                error_message=error_message,
                surface=ctx.session_surface(),
                session_cwd=ctx.session_cwd(),
            )

    return observe_gate


def run_probes(ctx: Any, log: EvidenceLog, platform: str, session_id: str) -> str:
    """`/lverify p0`: run the probes a slash command can run, then show all evidence."""
    status = ctx.post_message("loopverify P0: status line posted")
    edited = status.edit("loopverify P0: status line edited in place")
    log.record(
        "status-line",
        platform=platform,
        posted=status.ok,
        edited=bool(edited.get("edited")),
        fallback=bool(edited.get("fallback")),
        error=status.error,
    )
    log.record(
        "command-context",
        platform=platform,
        has_session_id=bool(session_id),
        surface=ctx.session_surface(),
        session_cwd=ctx.session_cwd(),
    )
    return "\n".join(_describe(entry) for entry in log.read())


def open_report(log: EvidenceLog, report_path: Path, platform: str) -> str:
    """`/lverify report`: render the evidence as HTML and open it in the host browser."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_html(log.read()), encoding="utf-8")
    if platform not in LOCAL_SURFACES:
        return f"Report written to {report_path}. It only opens in a browser from a local session."
    opened = webbrowser.open(report_path.as_uri())
    log.record("report", platform=platform, browser_opened=opened, path=str(report_path))
    return f"Report written to {report_path}; browser opened: {opened}."


def _describe(entry: dict[str, Any]) -> str:
    facts = ", ".join(
        f"{key}={value!r}" for key, value in entry.items() if key not in ("at", "probe")
    )
    return f"{entry['at']} {entry['probe']}: {facts}"


def _render_html(entries: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        "<tr><td>{at}</td><td>{probe}</td><td><code>{facts}</code></td></tr>".format(
            at=html.escape(entry["at"]),
            probe=html.escape(entry["probe"]),
            facts=html.escape(
                json.dumps({k: v for k, v in entry.items() if k not in ("at", "probe")})
            ),
        )
        for entry in entries
    )
    return (
        "<!doctype html>\n<html><head><meta charset='utf-8'><title>loopverify P0</title></head>\n"
        "<body><h1>loopverify P0 evidence</h1>\n"
        "<table border='1' cellpadding='4'>\n"
        "<tr><th>at</th><th>probe</th><th>observed</th></tr>\n"
        f"{rows}\n</table></body></html>\n"
    )
