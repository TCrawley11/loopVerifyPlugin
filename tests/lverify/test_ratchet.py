from loopverify.ratchet import Finding, gone_count, new_findings


def lint(path: str = "a.py", code: str = "F401", message: str = "`os` unused", line: int = 1):
    return Finding("lint", path, code, message, line)


def test_a_finding_present_at_the_base_is_not_new():
    assert new_findings([lint()], [lint()]) == []


def test_moving_a_finding_to_another_line_does_not_make_it_new():
    assert new_findings([lint(line=40)], [lint(line=3)]) == []


def test_each_base_finding_excuses_only_one_copy():
    assert new_findings([lint(line=1), lint(line=9)], [lint()]) == [lint(line=9)]


def test_a_different_message_path_code_or_check_is_new():
    head = [
        lint(message="`sys` unused"),
        lint(path="b.py"),
        lint(code="E501"),
        Finding("types", "a.py", "F401", "`os` unused"),
    ]
    assert new_findings(head, [lint()]) == head


def test_parse_errors_are_always_new_even_when_the_base_has_them():
    ruff = lint(code="invalid-syntax", message="unexpected EOF")
    mypy = Finding("types", "a.py", "syntax", "Unexpected EOF")
    assert new_findings([ruff, mypy], [ruff, mypy]) == [ruff, mypy]


def test_new_findings_keep_head_order():
    head = [lint(path="z.py"), lint(), lint(path="b.py")]
    assert new_findings(head, [lint()]) == [lint(path="z.py"), lint(path="b.py")]


def test_gone_counts_base_findings_the_change_removed():
    base = [lint(), lint(line=2), lint(path="b.py")]
    assert gone_count([lint()], base) == 2
    assert gone_count(base, base) == 0
