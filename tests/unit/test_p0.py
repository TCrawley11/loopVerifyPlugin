import webbrowser

from plugin import commands, gate, p0

PROBE = {"command": f"{gate.PROBE_MARKER}=1 gh pr create --title probe"}


def test_evidence_log_round_trips_entries_in_order(log):
    log.record("first", n=1)
    log.record("second", ok=True)
    entries = log.read()
    assert [(entry["probe"], entry.get("n"), entry.get("ok")) for entry in entries] == [
        ("first", 1, None),
        ("second", None, True),
    ]


def test_evidence_log_is_empty_before_the_first_record(log):
    assert log.read() == []


def test_observer_records_how_hermes_handled_gh_pr_create(ctx, log):
    observe = p0.make_gate_observer(ctx, log)
    observe(
        tool_name="terminal",
        args=PROBE,
        result="{}",
        status="blocked",
        error_message=gate.BLOCK_MESSAGE,
        session_id="s1",
    )
    (entry,) = log.read()
    assert entry["probe"] == "gate"
    assert entry["status"] == "blocked"
    assert entry["surface"] == "cli"
    assert entry["session_cwd"] == ctx.cwd


def test_observer_ignores_every_other_tool_call(ctx, log):
    observe = p0.make_gate_observer(ctx, log)
    observe(tool_name="terminal", args={"command": "ls"}, status="ok")
    observe(tool_name="read_file", args={"path": "x"}, status="ok")
    assert log.read() == []


def test_p0_posts_one_status_line_then_edits_it(ctx, log, tmp_path):
    lverify = commands.make_handler(ctx, log, tmp_path / "data")
    reply = lverify("p0", platform="discord", session_id="s1")
    assert ctx.posts == [
        "loopverify P0: status line posted",
        "loopverify P0: status line edited in place",
    ]
    probes = {entry["probe"]: entry for entry in log.read()}
    assert probes["status-line"]["posted"] is True
    assert probes["status-line"]["edited"] is True
    assert probes["command-context"]["platform"] == "discord"
    assert probes["command-context"]["has_session_id"] is True
    assert "status-line" in reply and "command-context" in reply


def test_report_opens_the_browser_from_a_local_session(ctx, log, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    log.record("gate", status="blocked")
    lverify = commands.make_handler(ctx, log, tmp_path)
    reply = lverify("report", platform="cli")
    report = tmp_path / commands.REPORT_FILE
    assert opened == [report.as_uri()]
    assert "browser opened: True" in reply
    assert "blocked" in report.read_text()
    assert log.read()[-1]["browser_opened"] is True


def test_report_does_not_open_a_browser_for_a_messaging_platform(ctx, log, tmp_path, monkeypatch):
    def fail(url):
        raise AssertionError("a Discord session must not open the host browser")

    monkeypatch.setattr(webbrowser, "open", fail)
    lverify = commands.make_handler(ctx, log, tmp_path)
    reply = lverify("report", platform="discord")
    assert (tmp_path / commands.REPORT_FILE).exists()
    assert "only opens in a browser from a local session" in reply


def test_report_escapes_evidence_text(ctx, log, tmp_path, monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    log.record("gate", error_message="<script>alert(1)</script>")
    commands.make_handler(ctx, log, tmp_path)("report", platform="cli")
    assert "<script>" not in (tmp_path / commands.REPORT_FILE).read_text()
