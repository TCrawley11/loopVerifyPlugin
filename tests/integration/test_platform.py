"""P0: Hermes itself loads the plugin, honors the gate and exposes the skill and command."""

import json

from plugin import gate

PROBE = f"{gate.PROBE_MARKER}=1 gh pr create --title probe --body probe"


def test_hermes_loads_the_plugin_without_error(in_hermes):
    plugin = in_hermes("""
emit(next(p for p in get_plugin_manager().list_plugins() if p["name"] == "loopverify"))
""")
    assert plugin["enabled"] is True
    assert plugin["error"] is None
    assert (plugin["hooks"], plugin["commands"]) == (2, 1)


def test_bundled_skill_loads_as_loopverify_p0_probe(in_hermes):
    skill = in_hermes("""
emit(json.loads(handle_function_call("skill_view", {"name": "loopverify:p0-probe"})))
""")
    assert skill["success"] is True
    assert skill["name"] == "loopverify:p0-probe"
    assert "LVERIFY-SKILL-OK" in skill["content"]


def test_hermes_blocks_gh_pr_create_and_reports_the_call_as_blocked(in_hermes, hermes_home):
    result = in_hermes(f"""
emit(json.loads(handle_function_call("terminal", {{"command": {PROBE!r}}})))
""")
    assert result == {"error": gate.BLOCK_MESSAGE}
    evidence = hermes_home / "plugin-data" / "loopverify" / "p0-evidence.jsonl"
    (entry,) = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert (entry["probe"], entry["status"]) == ("gate", "blocked")


def test_hermes_still_runs_other_terminal_commands(in_hermes):
    result = in_hermes("""
emit(json.loads(handle_function_call("terminal", {"command": "echo lverify-ok"})))
""")
    assert result["exit_code"] == 0
    assert result["output"] == "lverify-ok"


def test_hermes_registers_the_lverify_slash_command(in_hermes):
    usage = in_hermes("""
emit(get_plugin_command_handler("lverify")(""))
""")
    assert usage.startswith("Usage:")
    assert "/lverify review" in usage
