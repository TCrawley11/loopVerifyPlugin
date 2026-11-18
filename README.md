# loopVerifyPlugin

loopVerifyPlugin is a Hermes plugin. It lets you "loop" with the agent(s) and work as a
teammate, while a verification layer acts as the judge of the work.

Status: **P1.** `lverify verify` and its debt rules work; the real PR gate and the chunk flow are
not built yet. The plan is in [PLAN.md](PLAN.md); what is proven so far is in
[docs/p0-report.md](docs/p0-report.md).

## What exists today

- Plugin id `loopverify`, slash command `/lverify`.
- A `pre_tool_call` gate hook. In P0 it only blocks a `gh pr create` that carries the marker
  `LVERIFY_P0_PROBE`, to prove that a hook can block the command.
- `/lverify review [repo] [base]`: an independent review of HEAD against `base` by Gemini 4
  Carbon through OpenRouter. The reviewer sees only the diff, is briefed to find what is wrong,
  and a finding only blocks when it comes with a reproducing command.
- `/lverify p0` and `/lverify report`: the P0 probes.
- One bundled skill, `loopverify:p0-probe`.

## lverify (command line)

`lverify` runs where the repo is (in the Hermes sandbox). It reads each project's checks from a
folder in a private state repo, so nothing lverify-related lives in the project itself.

```
lverify init --state-repo https://github.com/<you>/lverify-state.git   # once per clone
lverify verify                                                         # 0 pass, 1 fail, 2 setup
```

`verify` runs every check at HEAD and the differential ones (ruff, mypy) also at the merge base
with the configured base branch, and fails only on findings the change adds. A check's folder in
the state repo is named after the project's remote, e.g. `github.com/WE-Autopilot/ap1/config.toml`:

```toml
base = "origin/perception/perception_dev"

[[check]]
name = "lint"
parser = "ruff"                  # ruff | mypy | exit-code | debt
cwd = "src/perception"
command = ["uvx", "ruff@0.17.5", "check", "--output-format", "json", "."]

[[check]]
name = "debt"
parser = "debt"                  # built in, no command
cwd = "src/perception"
comment_max_lines = 3            # default
expiry_max_days = 90             # default
```

The `debt` check (Python files only so far) fails on workaround markers a change adds without a
justification: `# noqa`, `# type: ignore`, TODO/FIXME/XXX/HACK, an `except` that only passes,
and `time.sleep`. A justification is a comment on the marker's line or the line right above it:

```python
import foo  # noqa: F401  # debt(2026-12-31): re-exported for the plugin loader
```

with a date at most `expiry_max_days` ahead and a reason of at least three words. It also fails
on comment blocks longer than `comment_max_lines` (a block on line 1, such as a license header,
is exempt; docstrings are not counted). Expired markers fail in files the change touches, so you
pay them off or renew them; expired markers elsewhere are only listed.

## Install

```
hermes plugins install TCrawley11/loopVerifyPlugin --enable
```

Say yes to the capability prompt. The reviewer runs on OpenRouter whatever your active model
is, which needs `llm.provider_override` and `llm.model_override`, and an OpenRouter credential in
Hermes (`hermes auth add openrouter --api-key ...`).

## Development

```
uv sync
uv run pytest                                    # unit and lverify tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The checkout directory must be a valid Python package name (as `loopVerifyPlugin` is): Hermes
needs the `__init__.py` at the repo root, and pytest imports it as a package named after the
directory.

The integration tests run the plugin inside a real Hermes, so they need the interpreter of a
Hermes checkout. They are skipped under any other interpreter.

```
<hermes-venv>/bin/python -m pytest tests/integration
<hermes-venv>/bin/hermes plugins doctor . --ci
<hermes-venv>/bin/hermes plugins validate .
```

## License

MIT, see [LICENSE](LICENSE).

## Credits

The standard this plugin enforces is inspired by Lauren Tan's talk
["All Code Is Slop (Except Mine)"](https://www.youtube.com/watch?v=O_zcc6Xl12g) and by
[pstack](https://github.com/cursor/plugins/tree/main/pstack) (MIT).
