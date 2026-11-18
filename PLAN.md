# loopVerifyPlugin plan

Status: P0 in progress. Everything that can be proven from the sandbox is proven; the proofs
that need the live Hermes gateway are pending. See `docs/p0-report.md`.

## One-line pitch

loopVerifyPlugin is a Hermes plugin. It lets the user "loop" with the agent(s) and work as a
teammate, while a verification layer acts as the judge of the work.

## Names (decided)

- GitHub repo: `TCrawley11/loopVerifyPlugin`, public, MIT license.
- CLI: `lverify`. Tygo preferred `loop`, but Hermes already ships a built-in `/loop` slash command
  (re-runs a prompt on an interval), so `loop` would confuse. PyPI `loop` is also taken;
  `lverify` and `loopverify` are free. Use `lverify` for both CLI and slash command.
- Slash command: `/lverify ...` (NOT `/loop`).
- Python package / plugin id: `loopverify`.
- Per-project state: a folder in the private state repo `TCrawley11/lverify-state` (see
  "State repo"). Nothing is kept in the project's tree. `.lverify/` and `.ty/` are dead.
- Earlier draft names `tstack` and `ty_hermes` are dead.

## Tygo's three goals (priority order)

1. He must understand what "he" shipped well enough to explain it to others. Hands-on by default.
2. No compounding tech debt: long comments and one-off workarounds. Corrections become
   deterministic checks so they never repeat (Lauren Tan's core idea).
3. A real formal verification layer.

Works in PR-sized chunks, not epics. Max 2-3 agents at once (builder + independent reviewer).
No swarms. Prefers quality, simplicity and maintainability over dev cost. Never use em dashes.

## Chunk flow (hands-on is the default; skip only when he says so)

Skip phrases: "skip hands-on" (all), "skip design", "skip core", "skip teach".

1. Playback: agent restates goal and problem; he confirms.
2. Design checkpoint: data shapes, signatures, one diagram; he approves before code.
3. You write the core: agent picks the key piece, says why, scaffolds around it, leaves a failing
   test; he implements; agent reviews as a peer and runs verify. He may swap the piece.
4. Agent builds the rest, then `lverify verify` + reviewer sign-off.
5. Report card from one structured report (JSON) rendered by a script, never freehand: what changed
   and why, decisions with rejected alternatives, module diagram with touched parts highlighted,
   evidence (commands + outputs), open questions.
6. Teach-back: 2-3 questions checked against the actual code; explain gaps with the diagram.
7. Decision log update.

## Decision log

- One file per project: `DECISIONS.md` in the project's state folder, newest first, rolling window (default last 25
  entries or 30 days, configurable). Older entries drop off; git history keeps them. He wants
  yesterday's decisions, not last quarter's.
- Written only by `lverify decide` (insert + trim), never hand-edited.
- Entry: date, chunk, decision, alternatives rejected and why, reversible or not.

## Gates

- Agents commit and run `gh pr create` themselves. Commits are free.
- Opening a PR or pushing to a PR branch requires, tied to the exact commit SHA:
  1. a deterministic `lverify verify` pass (lint, types, comment/debt rules, tests, formal checks);
     the builder's self-report never counts;
  2. sign-off from a reviewer agent in a fresh context, written into the PR body.
- Reviewer model (decided 2026-11-17, replaces the earlier "second Opus instance on the same
  plan"): Gemini 4 Carbon through OpenRouter, model id `google/gemini-4-carbon`. A different
  model family than the builder removes the shared blind spots of a same-model reviewer. The
  structural rules stay, because independence still has to come from structure:
  - fresh context: the reviewer sees only the diff, the chunk spec/design, and `lverify verify`
    output, never the builder's transcript or reasoning;
  - adversarial brief: "find what is wrong", not "confirm it works";
  - a finding counts only with a reproducing command or failing test (pstack `interrogate` rule),
    so a confident but wrong reviewer cannot block a PR;
  - the deterministic gate stays first, so the reviewer covers judgment, not mechanics;
  - a verdict that Hermes routed to any other model (provider fallback) is rejected.
- The reviewer is one `ctx.llm.complete_structured` call from the plugin (`plugin/reviewer.py`):
  a one-shot completion with no tools and no conversation state, on Hermes-owned OpenRouter
  credentials. This answers the P0 question of how to run a reviewer with no access to the
  builder's history. It needs the `llm.provider_override` and `llm.model_override` capabilities,
  granted on the consent screen of `hermes plugins install` or `hermes plugins enable`.
- Review rounds converge (decided 2026-11-17, after rounds that each found new edge cases):
  - a finding blocks only with impact `breaks-intended-use`, a repro, and scope `new-or-changed`
    or `unfixed-previous-finding`; edge cases, hardening and already-reviewed code are notes;
  - a re-review (a rejected review of an ancestor of HEAD, same base) sees the previous
    findings and the diff since, so each round's blocking surface is the last fix; a rebase
    restarts at round 1;
  - at most 3 blocking findings and 5 notes, details of at most 80 words;
  - reasoning effort `medium` through the plugin's auxiliary task `lverify_review`, so reasoning
    cannot use up the output cap.
- Human overrule (decided 2026-11-17): `/lverify overrule [repo] <n>[,<n>...] <reason>` marks
  blocking findings of HEAD's review as not blocking. HEAD is cleared when every blocking finding
  is overruled; each overrule (finding, reason, who, when) goes into the PR body, and the next
  round sees it as decided so the reviewer does not raise it again. It is an escape hatch, not an
  approval step: nothing waits for it.
- Review and overrule records are sealed with an HMAC whose key lives only in the plugin's data
  dir on the host, which the sandbox does not mount, so an agent cannot forge a sign-off or an
  overrule by writing to `.git/lverify/reviews`. Unsealed or edited records are ignored.
- A plugin `pre_tool_call` hook checks both exist and blocks the command otherwise. No human
  approval step in the gate.

## Tech debt rules (only on lines a change adds)

- Comments only for a "why" the code cannot express, length-capped.
- Workaround markers (`noqa`, `type: ignore`, bare `except: pass`, sleeps as fixes, TODO) need a
  reason and an expiry, else fail.
- Existing debt goes in a baseline file; only new occurrences fail (ratchet).
- `/lverify correct`: fix each correction at the highest level that works (architecture > types >
  lint whose error names the fix > test > prose). Prove each new check fails on a real past
  mistake. `rules.md` in the project's state folder pairs each rule with its enforcer; a rule without one is a defect.
- Scope (decided): `.py` files only. Notebooks are out of scope for debt rules.
- Build the checker language-pluggable from day one: one adapter per language (file globs,
  comment syntax, workaround markers, linter/formatter commands). P1 ships only the Python adapter
  (ruff, pyright or mypy, markers `noqa`, `type: ignore`, bare `except: pass`, `time.sleep`).
- Planned next adapter: C++ for his ap1 ground-node work (clang-tidy, clang-format, markers
  `NOLINT`, `// TODO`, `std::this_thread::sleep_for` as a fix). ap1 is a shared team repo, so
  there the rules start as local-only config for his own changes, not imposed on the team.

## Formal verification tiers

- Default: property-based tests (Hypothesis).
- State machines and protocols: TLA+ model checking. Tygo wants to READ the models, so author them
  in PlusCal (Lamport's pseudocode-like algorithm language that the TLA+ tools translate to TLA+,
  then TLC checks). Also render as a Mermaid statechart (pstack `statechart` idea). Quint is a
  readable alternative worth one look in P4.
- Every property must be shown able to fail (break it on purpose, confirm the checker catches it).
- Each spec records which source files it covers; editing those files flags the spec for recheck.
- Honest fit for the ML pilot: mostly property tests. Good candidates:
  - BPE tokenizer: `decode(encode(x)) == x` round trip; merges are deterministic.
  - Causal attention: output at position t is unchanged when tokens after t change.
  - Attention weights: rows sum to 1, masked entries are 0; shapes per config.
  - Dataloader: windows/stride cover the token stream as specified, no off-by-one.
  - PlusCal/TLA+ candidate: training checkpoint/resume (gradient checkpoints exist), i.e. resume
    after interruption never skips or repeats a step/batch.

## Surfaces (desktop app: no implementation for now)

- CLI (primary): compact text report in the terminal; `/lverify report` opens the HTML report in
  the browser. Slash command handlers run in the gateway on the host, so opening the browser should
  work even though terminal tools run in the docker sandbox (P0 item c, pending on the host).
- Discord (away from PC, phone). He will not write code from the phone. Design for:
  - Probing questions about his projects ("how does my BPE merge step work?", "where did I leave
    the ViT?"): read-only answers in the style of pstack `how`/`why`. Consider a hook that keeps
    Discord sessions read-only on repo files unless he explicitly allows edits.
  - One-line progress reports, e.g.
    `ml-experiments/vit-patch-embed: step 3/7, core waiting on you, verify pass, 2 new decisions`.
    Plugin `ctx.post_message` + `handle.edit` can keep one live status line instead of spamming.
  - Resume elsewhere: `/lverify park` commits WIP to a branch, pushes, and writes
    `RESUME.md` in the project's state folder (where you are in the chunk flow, next step, open questions, last report).
    On the laptop: `lverify resume`.
  - Decided: the laptop runs its OWN Hermes. Hermes sessions, memory and local skills do not
    transfer, so git is the only carrier: the WIP branch carries the code, the state repo carries
    `RESUME.md`, `DECISIONS.md` and the last report JSON. The plugin is installed on both machines
    (`hermes plugins install TCrawley11/loopVerifyPlugin`) and bundles its skills, so the laptop
    gets the same workflow without copying `~/.hermes/skills`. `resume` must refuse to clobber
    local uncommitted work.
  - PNG version of the report card when he asks for detail.
- Desktop app: later. Known useful tools when it happens: `desktop_preview` (HTML in a pane beside
  chat) and `gui_tour` (narrated highlights, good for teach-back). Desktop-session only.

## State repo (decided 2026-11-17)

- One private repo, `TCrawley11/lverify-state`, one folder per project named after its remote:
  `github.com/WE-Autopilot/ap1/` holds `config.toml` (the checks), and later `DECISIONS.md`,
  `RESUME.md`, `rules.md` and reports.
- Each project repo keeps its own small clone at `<git dir>/lverify/state`, connected once with
  `lverify init --state-repo <url>` (stored as local git config `lverify.stateRepo`). The git dir
  is reachable wherever the repo is mounted and is never pushed.
- Cost control: the clone is partial (`--filter=blob:none --sparse`: commits and trees only, file
  contents for this project's folder alone), and it is fetched at most once an hour. When
  offline, lverify uses the cached copy and waits a full hour before trying again.
- Measured on a clone of ap1 against a local state repo: the first verify (clone, base
  extraction, ruff at base and HEAD) took 0.12 s, a warm one 0.08 s; the state clone is 148 KB.
  Over the network, a partial clone of a small GitHub repo took 0.5 s and a no-op fetch 0.2 s.
- Rejected: auto-loading the state repo when Hermes starts in a recognized directory. With the
  cache above, verify pays at most half a second of network once an hour, which does not justify
  a host-side hook, host credentials and a background thread.
- LLM tokens: `lverify verify` prints one line per check and at most 20 new findings per check;
  raw tool output only when a check errors. `--json` gives the full report.

## Pilot repo: WE-Autopilot/ap1 (decided 2026-11-17, replaces ml-experiments)

- Chosen because it is how Tygo will work from now on: a shared, public team monorepo (ROS 2
  Jazzy), with work on feature branches merged into a team staging branch
  (perception: `perception/perception_dev`), and CODEOWNERS approval from the team leads.
- Hard constraint: nothing lverify-related goes upstream. No files in ap1's tree, no lverify
  commits on branches pushed to WE-Autopilot, no CI changes. The one exception is the reviewer's
  sign-off in the PR body (decided 2026-11-17, format to be tuned). lverify state
  for ap1 lives outside the repo; stamps and caches live in ap1's `.git` dir, which is never
  pushed. The rules apply to Tygo's own changes only.
- His ap1 Python lives in `src/perception` (an ament_python package with its own `pyproject.toml`
  and `uv.lock`). Four modules and the tests import ROS packages (rclpy, cv_bridge), so checks
  need a ROS Jazzy environment in the sandbox. C++ (ground node) comes with the C++ adapter.
- Consequence: there is no required CI check on ap1, so the local gate is the only gate there.
- ml-experiments stays a possible second target for in-repo use later.

## Hermes mechanisms (checked against the docs and Hermes source at commit 40d1e95f)

- Plugin = dir with `plugin.yaml` + `__init__.py` with `register(ctx)`; installed under
  `~/.hermes/plugins/` or `hermes plugins install owner/repo`, then `hermes plugins enable <name>`.
  Project-local `./.hermes/plugins/` is NOT loaded by the gateway. The manifest must sit at the
  install root; `owner/repo/subdir` installs exist but download only that folder.
- `ctx.register_skill(name, path)`: bundled skills load as `loopverify:<name>`.
- `ctx.register_command(...)`: slash commands, run in the gateway; return text, don't print. A
  handler receives `platform`, `session_id`, `session_key` and `source` when it declares them.
- `ctx.register_hook("pre_tool_call", ...)`: returning `{"action": "block", "message": ...}`
  blocks the tool call and the message becomes the tool result. A hook that raises or times out
  also blocks (fail closed), so the hook must never raise on calls it does not care about.
  `{"action": "modify", "args": {...}}` rewrites the tool arguments.
- `post_tool_call` reports `status="blocked"` for a call a plugin blocked.
- `ctx.session_cwd()` and `ctx.session_surface()` give the calling session's working directory
  and client (`cli`, `tui`, `desktop`, or the messaging platform) inside hooks and tools.
- `ctx.post_message(text)` -> handle with `.edit(text)`; `ctx.inject_message`.
- `ctx.llm.complete*`: host-owned model call. `provider=` and `model=` overrides are denied until
  the user grants `llm.provider_override` / `llm.model_override`. Hermes omits `temperature` for
  Gemini 3.6+ models, so the reviewer cannot pin sampling.
- `plugins.plugin_storage.plugin_data_dir(name)`: host-side state under
  `~/.hermes/plugin-data/<name>/`, which the docker sandbox cannot write.
- Middleware (`ctx.register_middleware("tool_request"/"tool_execution", ...)`) can rewrite or wrap
  tool calls; alternative gate mechanism.
- Project skills in a target repo need `hermes skills trust` once; precedence project > local >
  create_dir > external_dirs.
- Config changes: Tygo runs `hermes config set ...` on the host; never hand-edit config.yaml.

## Environment facts

- Hermes runs on Tygo's host (Linux, CachyOS). Terminal tools run in a docker sandbox: user `pn`,
  `/workspace` = the dir the session started in. The sandbox has `gh` and `git`, no
  claude/codex/docker. The `lverify` CLI and its checks must run in the sandbox (where the repo is
  mounted), so the sandbox image needs the toolchains (ruff, pyright/mypy, pytest, Hypothesis,
  Java + tla2tools for PlusCal/TLC, mermaid-cli or graphviz for diagrams).
- The sandbox has no LLM credentials. Model calls go through the plugin on the host.
- Python everywhere (Hermes plugins are Python).

## Repo layout

- `plugin.yaml`, `__init__.py`: the Hermes manifest and entry point. They sit at the repo root
  because `hermes plugins install TCrawley11/loopVerifyPlugin` needs the manifest at the install
  root. The entry point only re-exports `plugin.register`.
- `plugin/`: hooks (gate, later read-only-on-Discord), `/lverify` commands, the reviewer, skill
  registration.
- `skills/`: bundled skills. Planned: chunk, report, correct, teach-back, formal, verify-setup,
  plus adapted pstack principles (MIT, credit Lauren Tan / cursor/plugins).
- `tests/unit/`: run anywhere. `tests/integration/`: run the plugin inside a real Hermes.
- `docs/`: `p0-report.md`.
- `loopverify/`: the `lverify` CLI, which runs in the sandbox. Today: `init`, `verify`. Planned:
  `decide`, `report render`, `rules check`, `debt check`, `park`, `resume`, `status`.
  `tests/lverify/` tests it against real git repos and a file:// state repo.
- Planned: `templates/` (a starter `config.toml` and `rules.md` for a state folder); `docker/`
  (sandbox image with toolchains).

## Phases

- P0 (prove the platform): create the public repo with `PLAN.md` and a hello-world plugin. Prove:
  (a) a plugin hook blocks `gh pr create` in this setup, (b) bundled skills load as
  `loopverify:*`, (c) `/lverify report` opens the host browser from the CLI, (d) a one-line status
  message posts and edits in Discord, (e) whether hook/command context exposes the platform
  (needed for Discord read-only mode), (f) the reviewer call reaches Gemini 4 Carbon on
  OpenRouter. Report each as proven or not, with evidence: `docs/p0-report.md`.
- P1: in four chunks.
  - P1.1 (done): `lverify verify` with the differential check, config from the state repo,
    stamps in the git dir.
  - P1.2 (done): the built-in `debt` check (`loopverify/debt.py`, Python adapter in
    `loopverify/debt_python.py`): markers need `# debt(YYYY-MM-DD): reason` (at most 90 days
    ahead, at least 3 words), comment blocks at most 3 lines (docstrings and a block on line 1
    exempt), differential like lint. Expired markers fail in touched files and are listed
    elsewhere. Debt findings at the base are cached per day because expiry depends on the date.
  - P1.3 (in progress): the `lverify_review` tool (the plugin runs `lverify verify --json` in the
    session sandbox through the terminal tool, then the reviewer; one sealed record per commit
    holds both), the pre_tool_call gate on `gh pr create` and pushes to PR branches (needs that
    record cleared and `lverify-signoff: <sha>` in the body), and `lverify init
    --guard-upstream` (pushes carrying `.lverify/` or `lverify*` files are blocked). The hook
    finds the repo from `workdir`, a leading `cd`, or the session sandbox's cwd (Hermes
    internal `get_active_env`, pinned by an integration test), maps it to the host through the
    docker mount, and fails closed when it cannot. Core `gate.decide` is Tygo's.
  - P1.4: ap1 wiring: its state folder, a persistent ROS Jazzy environment in the sandbox, first
    verify on a perception branch.
- P2: chunk flow, report card (terminal + HTML + PNG), teach-back, you-write-the-core,
  `lverify decide`, `park`/`resume`, Discord one-liners. First real chunk: an ap1 perception change.
- P3: `/lverify correct` and the rules ledger.
- P4: formal layer: Hypothesis properties above, then PlusCal checkpoint/resume model + statechart.
- Later: desktop app (preview pane report, gui_tour teach-back), optional kanban queue.

## Decided after planning (2026-11-17)

- Laptop runs its own Hermes; park/resume carries state through git only.
- Debt rules: `.py` only for now; C++ adapter planned for ap1 ground-node work.
- Reviewer: Gemini 4 Carbon through OpenRouter (`google/gemini-4-carbon`), called with `ctx.llm`.
  This replaces "second Opus instance on the same plan; OpenRouter optional later".
- `plugin.yaml` and the entry `__init__.py` live at the repo root, code in `plugin/`.
- Type checker for P1: mypy.
- Pilot repo: WE-Autopilot/ap1, with nothing lverify-related pushed upstream.
- License: MIT.
- Differential check instead of a stored baseline: verify runs differential checks (ruff, mypy)
  at HEAD and at the merge base with the configured base branch; a finding fails only if HEAD
  has more of it. A stored baseline goes stale on a shared repo every time teammates merge.
  Parse errors, crashes and failing exit-code checks (tests) are never excused by the base.
- A finding's identity is (check, path, code, message), without the line number.
- Check commands are configured per project (they may need a ROS environment); lverify only owns
  the parsers (`ruff`, `mypy`, `exit-code`).
- Format checking is off for ap1: the team has no Python formatter.
- Reviewer sign-off goes in the PR body, on shared repos too.

## Open questions for Tygo

Before P1.3:

- Gate design, from P0 findings:
  - The gate hook sees the command text and `workdir`, not the shell's current directory (the
    sandbox shell keeps its cwd between calls). So the hook cannot tell which repo a bare
    `gh pr create` targets. Options: rewrite the command with `modify` so the check runs in the
    shell (`lverify gate -- gh pr create ...`), or require an explicit repo.
  - The builder can write anything inside the repo and its git dir. A stamp kept there can be
    forged by the builder. `plugin_data_dir` on the host is out of the
    sandbox's reach and is the natural place for the plugin's own records.
  - Who runs the reviewer's repro commands, and where (the sandbox, through `ctx.dispatch_tool`).
  - A local hook is a guardrail, not enforcement. Real enforcement is a required CI check on the
    GitHub side.

## Sources

- Talk: "All Code Is Slop (Except Mine)", Lauren Tan, https://www.youtube.com/watch?v=O_zcc6Xl12g
- pstack: https://github.com/cursor/plugins/tree/main/pstack (MIT). Most relevant skills: correct,
  no-comments, teach, how, why, ketchup, in-your-own-words, show-me-your-work, statechart,
  create-formal-verification-skill, principle-encode-lessons-in-structure, principle-prove-it-works.
- Hermes docs index: https://hermes-agent.nousresearch.com/docs/llms.txt (full: llms-full.txt).
