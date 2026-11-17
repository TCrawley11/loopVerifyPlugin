"""`lverify verify`: run a project's checks and judge only what the change introduces.

Differential checks run twice: at HEAD and at the merge base with the configured base branch.
A finding fails only when HEAD has more of it than the merge base. Base results are cached by
commit, so after the first run against a base only HEAD is checked again.
"""

import hashlib
import io
import json
import shutil
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path

from . import checks, config, git, state
from .errors import SetupError
from .ratchet import Finding, gone_count, new_findings

REPORT_VERSION = 1


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: tuple[str, ...]
    differential: bool
    new: tuple[Finding, ...]
    head_count: int
    gone: int
    error: str | None
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.error is None and not self.new


@dataclass(frozen=True)
class Report:
    version: int
    sha: str
    base_ref: str
    base_sha: str
    clean: bool
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)


@dataclass(frozen=True)
class Outcome:
    report: Report
    project: state.ProjectState
    stamp: Path | None


def verify(repo: Path, *, force_refresh: bool = False) -> Outcome:
    root = git.toplevel(repo)
    home = git.common_dir(root) / "lverify"
    project = state.open_state(root, force_refresh=force_refresh)
    cfg = config.load(project.config_file)
    sha, clean = git.head_sha(root), git.is_clean(root)
    try:
        base_sha = git.merge_base(root, cfg.base)
    except git.GitError as exc:
        raise SetupError(f"cannot find the merge base with {cfg.base!r}; fetch it first") from exc

    base = _BaseTree(root, home, base_sha, cfg.checks)
    results = tuple(_check(spec, root, home, base) for spec in cfg.checks)
    report = Report(REPORT_VERSION, sha, cfg.base, base_sha, clean, results)
    stamp = _write_stamp(home, report) if clean else None
    return Outcome(report, project, stamp)


def render(outcome: Outcome, max_listed: int = 20) -> str:
    report, project = outcome.report, outcome.project
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        f"lverify verify: {verdict}  {report.sha[:10]} vs {report.base_ref} "
        f"(merge base {report.base_sha[:10]})",
        f"  project {project.key}, state {'refreshed' if project.refreshed else 'cached'}",
    ]
    if project.warning:
        lines.append(f"  warning: {project.warning}")
    width = max(len(result.name) for result in report.results)
    for result in report.results:
        lines.append(f"  {result.name:<{width}}  {_status(result)}")
        if result.error:
            lines.extend(f"      {line}" for line in result.output_tail.splitlines())
        for finding in result.new[:max_listed]:
            lines.append(f"      {finding.describe()}")
        if len(result.new) > max_listed:
            lines.append(f"      ... {len(result.new) - max_listed} more")
    if outcome.stamp is not None:
        lines.append(f"  stamp {outcome.stamp}")
    else:
        lines.append("  not stamped: the working tree has uncommitted changes")
    return "\n".join(lines)


def to_json(outcome: Outcome) -> str:
    return _report_json(outcome.report)


def _report_json(report: Report) -> str:
    return json.dumps({**asdict(report), "passed": report.passed}, indent=2)


def _status(result: CheckResult) -> str:
    if result.error:
        return f"ERROR {result.error}"
    word = "pass " if result.passed else "FAIL "
    if not result.differential:
        return word.strip()
    return f"{word} {len(result.new)} new ({result.head_count} at HEAD, {result.gone} fixed)"


def _check(spec: config.CheckSpec, root: Path, home: Path, base: "_BaseTree") -> CheckResult:
    differential = spec.parser in checks.DIFFERENTIAL_PARSERS

    def result(
        new: list[Finding], head: int, gone: int, error: str | None, tail: str = ""
    ) -> CheckResult:
        return CheckResult(
            spec.name, spec.command, differential, tuple(new), head, gone, error, tail
        )

    head = checks.run(spec, root, home / "cache" / "tools" / "head")
    if head.error:
        return result([], 0, 0, head.error, head.output_tail)
    if not differential:
        return result(new_findings(head.findings, ()), len(head.findings), 0, None)
    base_run = base.findings(spec)
    if base_run.error:
        return result(
            [], len(head.findings), 0, f"at the merge base: {base_run.error}", base_run.output_tail
        )
    introduced = new_findings(head.findings, base_run.findings)
    return result(
        introduced, len(head.findings), gone_count(head.findings, base_run.findings), None
    )


class _BaseTree:
    """The merge base's files for differential checks, extracted once and cached by commit."""

    def __init__(
        self, root: Path, home: Path, sha: str, specs: tuple[config.CheckSpec, ...]
    ) -> None:
        self.root, self.home, self.sha = root, home, sha
        self.cwds = sorted({s.cwd for s in specs if s.parser in checks.DIFFERENTIAL_PARSERS})
        self.tree = home / "base-tree"
        self.cache = home / "cache" / "base" / sha

    def findings(self, spec: config.CheckSpec) -> checks.Run:
        cached = self.cache / f"{spec.name}-{_digest(spec)}.json"
        if cached.exists():
            return checks.Run(tuple(Finding(**item) for item in json.loads(cached.read_text())))
        self._extract()
        if not (self.tree / spec.cwd).is_dir():
            run = checks.Run(())
        else:
            run = checks.run(spec, self.tree, self.home / "cache" / "tools" / "base")
        if run.error is None:
            self._prune_other_bases()
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps([asdict(f) for f in run.findings]))
        return run

    def _extract(self) -> None:
        marker = self.tree / ".lverify-base"
        wanted = json.dumps({"sha": self.sha, "cwds": self.cwds})
        if marker.exists() and marker.read_text() == wanted:
            return
        shutil.rmtree(self.tree, ignore_errors=True)
        self.tree.mkdir(parents=True)
        paths = [] if "." in self.cwds else [c for c in self.cwds if self._exists_at_base(c)]
        if "." in self.cwds or paths:
            archive = git.git_bytes(self.root, "archive", "--format=tar", self.sha, "--", *paths)
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(self.tree, filter="data")
        marker.write_text(wanted)

    def _exists_at_base(self, path: str) -> bool:
        return bool(git.git(self.root, "ls-tree", "-d", self.sha, "--", path).strip())

    def _prune_other_bases(self) -> None:
        for other in self.cache.parent.glob("*"):
            if other != self.cache:
                shutil.rmtree(other, ignore_errors=True)


def _digest(spec: config.CheckSpec) -> str:
    key = json.dumps([spec.parser, list(spec.command), spec.cwd])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _write_stamp(home: Path, report: Report) -> Path:
    path = home / "verify" / f"{report.sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_report_json(report))
    return path
