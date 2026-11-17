"""The `lverify` command line."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import git, state, verify
from .errors import SetupError

EXIT_PASS, EXIT_FAIL, EXIT_SETUP = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (SetupError, git.GitError) as exc:
        print(f"lverify: {exc}", file=sys.stderr)
        return EXIT_SETUP


def _init(args: argparse.Namespace) -> int:
    root = git.toplevel(args.repo)
    if args.state_repo:
        git.git(root, "config", "--local", state.STATE_REPO_KEY, args.state_repo)
    project = state.open_state(root, force_refresh=True)
    print(f"project {project.key}")
    print(f"state   {project.folder}")
    if project.warning:
        print(f"warning: {project.warning}")
    if not project.config_file.exists():
        print(f"no verify config yet: add {project.key}/{state.CONFIG_FILE} to the state repo")
    return EXIT_PASS


def _verify(args: argparse.Namespace) -> int:
    outcome = verify.verify(args.repo, force_refresh=args.refresh)
    print(verify.to_json(outcome) if args.json else verify.render(outcome))
    return EXIT_PASS if outcome.report.passed else EXIT_FAIL


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lverify")
    commands = parser.add_subparsers(required=True, metavar="command")

    init = commands.add_parser("init", help="connect this repo to its folder in the state repo")
    init.add_argument("--state-repo", help="URL of the state repo, stored in local git config")
    init.set_defaults(run=_init)

    check = commands.add_parser("verify", help="run the checks; fail only on new findings")
    check.add_argument("--refresh", action="store_true", help="fetch the state repo now")
    check.add_argument("--json", action="store_true", help="print the full report as JSON")
    check.set_defaults(run=_verify)

    for sub in (init, check):
        sub.add_argument("--repo", type=Path, default=Path.cwd(), help="default: current dir")
    return parser


if __name__ == "__main__":
    sys.exit(main())
