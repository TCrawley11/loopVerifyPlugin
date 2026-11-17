"""The Python adapter of the debt check: markers from comments (tokenize) and code (ast)."""

import ast
import io
import re
import tokenize
from dataclasses import dataclass

from .debt import COMMENT, JUSTIFICATION, DebtRules, Marker, marker_findings
from .ratchet import Finding

COMMENT_MARKERS = (
    ("noqa", re.compile(r"#\s*noqa\b", re.IGNORECASE)),
    ("type: ignore", re.compile(r"#\s*type:\s*ignore\b")),
    ("TODO", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")),
)
SOURCE_CHARS = 100


@dataclass(frozen=True)
class PythonAdapter:
    check: str
    suffixes: tuple[str, ...] = (".py",)

    def scan(self, path: str, text: str, rules: DebtRules) -> list[Finding]:
        lines = text.splitlines()
        try:
            comments = _comments(text)
            tree = ast.parse(text)
        except (SyntaxError, tokenize.TokenError, ValueError):
            # lint reports a file that does not parse, and never excuses it.
            return []
        full_line = {n for n in comments if lines[n - 1].lstrip().startswith("#")}

        def justification(line: int) -> str | None:
            own = comments.get(line)
            if own is not None and JUSTIFICATION.search(own):
                return own
            above = line - 1
            if above in full_line and JUSTIFICATION.search(comments[above]):
                return comments[above]
            return own

        def source(line: int) -> str:
            return lines[line - 1].strip()[:SOURCE_CHARS]

        markers = [
            Marker(kind, line, source(line), justification(line))
            for line, comment in sorted(comments.items())
            for kind, pattern in COMMENT_MARKERS
            if pattern.search(comment)
        ]
        markers += [
            Marker(kind, line, source(line), justification(line))
            for kind, line in _code_markers(tree)
        ]
        findings = marker_findings(self.check, path, markers, rules)
        findings += _long_comment_blocks(self.check, path, comments, full_line, rules)
        return sorted(findings, key=lambda finding: finding.line)


def _comments(text: str) -> dict[int, str]:
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return {tok.start[0]: tok.string for tok in tokens if tok.type == tokenize.COMMENT}


def _code_markers(tree: ast.AST) -> list[tuple[str, int]]:
    sleep_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "time"
        for alias in node.names
        if alias.name == "sleep"
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and all(_is_noop(stmt) for stmt in node.body):
            found.append(("swallowed exception", node.lineno))
        elif isinstance(node, ast.Call) and _is_sleep(node.func, sleep_names):
            found.append(("time.sleep", node.lineno))
    return sorted(found, key=lambda item: item[1])


def _is_noop(stmt: ast.stmt) -> bool:
    """`pass`, `...` or a bare string: none of them handle the exception."""
    if isinstance(stmt, ast.Pass):
        return True
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)


def _is_sleep(func: ast.expr, sleep_names: set[str]) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "sleep" and isinstance(func.value, ast.Name) and func.value.id == "time"
    return isinstance(func, ast.Name) and func.id in sleep_names


def _long_comment_blocks(
    check: str, path: str, comments: dict[int, str], full_line: set[int], rules: DebtRules
) -> list[Finding]:
    """Runs of full-line comments longer than the cap. A run that starts on line 1 is a file
    header (shebang, license) and is exempt; a debt justification line does not count."""
    counted = sorted(n for n in full_line if not JUSTIFICATION.search(comments[n]))
    findings = []
    run: list[int] = []
    for line in [*counted, 0]:
        if run and line == run[-1] + 1:
            run.append(line)
            continue
        if len(run) > rules.comment_max_lines and run[0] != 1:
            first = comments[run[0]].strip()[:SOURCE_CHARS]
            message = f"comment block of {len(run)} lines, max {rules.comment_max_lines}: {first}"
            findings.append(Finding(check, path, COMMENT, message, run[0]))
        run = [line]
    return findings
