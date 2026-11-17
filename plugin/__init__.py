"""Wires loopverify into Hermes: bundled skills, the PR gate hook and /lverify."""

from pathlib import Path
from typing import Any

from . import commands, gate, p0, reviewer

PLUGIN_NAME = "loopverify"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def register(ctx: Any) -> None:
    from plugins.plugin_storage import plugin_data_dir

    data_dir = plugin_data_dir(PLUGIN_NAME)
    log = p0.EvidenceLog(data_dir / "p0-evidence.jsonl")

    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        ctx.register_skill(skill_md.parent.name, skill_md)

    ctx.register_auxiliary_task(
        reviewer.TASK,
        display_name="loopverify reviewer",
        description="Independent review of a change before its PR (/lverify review).",
        defaults=reviewer.TASK_DEFAULTS,
    )
    ctx.register_hook("pre_tool_call", _gate_hook)
    ctx.register_hook("post_tool_call", p0.make_gate_observer(ctx, log))
    ctx.register_command(
        "lverify",
        commands.make_handler(ctx, log, data_dir),
        description="loopverify: report, review, overrule, p0",
        args_hint="<report|review|overrule|p0> [args]",
    )


def _gate_hook(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    return gate.decide(tool_name, args)
