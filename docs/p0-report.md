# P0 report: does Hermes do what the plan assumes?

Date: 2026-11-17. Plugin commit: see `git log`. Hermes reference: NousResearch/hermes-agent at
commit `40d1e95f` (2026-11-17), installed in the docker sandbox with an isolated `HERMES_HOME`.

Two levels of proof are used below, and they are not the same thing:

- **Sandbox Hermes**: the plugin runs inside a real Hermes (real plugin loader, real tool
  dispatch) in the sandbox. This proves the mechanism. It is repeatable:
  `<hermes-venv>/bin/python -m pytest tests/integration`.
- **Live gateway**: Tygo's own Hermes on the host, with the docker terminal backend, a real
  session, Discord and a browser. This is "this setup" in the plan. The sandbox cannot reach it,
  so every live proof is still pending and needs the host steps at the end.

| Item | Assumption | Sandbox Hermes | Live gateway |
|---|---|---|---|
| a | A plugin hook blocks `gh pr create` | proven | pending |
| b | Bundled skills load as `loopverify:*` | proven | pending |
| c | `/lverify report` opens the host browser | not provable here | pending |
| d | A status message posts and edits in Discord | not provable here | pending |
| e | Hook and command context expose the platform | documented, probe built | pending |
| f | Reviewer call reaches Gemini 4 Carbon on OpenRouter | proven up to a stub endpoint | pending |

## a. A plugin hook blocks `gh pr create`

Proven in sandbox Hermes. `tests/integration/test_platform.py` calls Hermes' own
`model_tools.handle_function_call`, the function every tool call goes through:

```
handle_function_call("terminal", {"command": "LVERIFY_P0_PROBE=1 gh pr create --title probe --body probe"})
-> {"error": "loopverify gate: blocked `gh pr create` (P0 probe). From P1 this needs a verify stamp and a reviewer sign-off for HEAD."}

handle_function_call("terminal", {"command": "echo lverify-ok"})
-> {"output": "lverify-ok", "exit_code": 0, "error": null}
```

The `post_tool_call` observer recorded what Hermes did with the call:

```
{"probe": "gate", "status": "blocked", "error_message": "loopverify gate: blocked `gh pr create` (P0 probe). ...", "surface": "", "session_cwd": ""}
```

The P0 gate only blocks a command that carries `LVERIFY_P0_PROBE`, so enabling the plugin does
not stop real PRs before P1 ships the real condition.

Pending live: the same block inside a session that uses the docker terminal backend.

## b. Bundled skills load as `loopverify:*`

Proven in sandbox Hermes, through the real `skill_view` tool:

```
handle_function_call("skill_view", {"name": "loopverify:p0-probe"})
-> success: true, name: "loopverify:p0-probe", content contains "LVERIFY-SKILL-OK"
```

Pending live: the agent loads `loopverify:p0-probe` in a session.

## c. `/lverify report` opens the host browser

Not provable from the sandbox: it needs the gateway process and a display. The command is built
and unit-tested (it writes the HTML, opens it only for local sessions, and never opens a browser
for a messaging platform). Whether `webbrowser.open` works from the gateway process is exactly
what the live run answers.

## d. A status message posts and edits in Discord

Not provable from the sandbox: it needs the live gateway and Discord. `/lverify p0` posts one
line with `ctx.post_message` and edits it with `handle.edit`, then records `posted`, `edited` and
`fallback`. The docs say Discord edits in place and the classic CLI prints each edit as a new
line at the end of the next turn.

## e. Hook and command context expose the platform

Documented, not yet observed live. Two mechanisms:

- Slash command handlers receive `platform` (and `session_id`, `session_key`, `source`) as
  keyword arguments when they declare them. `/lverify p0` records it.
- Hooks and tools call `ctx.session_surface()` (`cli`, `tui`, `desktop`, or the messaging
  platform) and `ctx.session_cwd()`. The gate observer records both for every `gh pr create`.
  `pre_tool_call` itself carries no `platform` field.

In the sandbox run there is no session, so both came back empty, as the docs say they should
outside a session. The live run shows the real values.

## f. Reviewer call reaches Gemini 4 Carbon on OpenRouter

The reviewer is `ctx.llm.complete_structured(provider="openrouter",
model="google/gemini-4-carbon", ...)`. The model id was read from OpenRouter's model list
(`google/gemini-4-carbon`, "Google: Gemini 4 Carbon", 1,048,576 tokens of context).

Proven in sandbox Hermes (`tests/integration/test_reviewer_call.py`):

- Without consent Hermes refuses the override, and `/lverify review` says how to grant it:
  `Plugin 'loopverify' cannot override the provider`.
- After the consent screen's own `record_consent`, the call passes the trust gate and reaches
  Hermes' provider call with `provider="openrouter"`, `model="google/gemini-4-carbon"`, a
  `json_schema` response format, the adversarial brief as the system message, and a user message
  holding only the instructions, the chunk spec, the verify output and the diff.

Observed once against a stub HTTP server standing in for openrouter.ai (one-off script, not kept
in the repo because it patches Hermes' base URL constant):

```
/lverify review /tmp/lv-repo main
-> Review of 95612407f83fc03ab21c82144927f4d706b1830c against main
   REJECTED, blocking findings: 1, by openrouter:google/gemini-4-carbon
   ...
   repro: python -c 'import x; assert x.add(1,2)==3'

request the stub received:
  POST /api/v1/chat/completions
  Authorization: Bearer <key from Hermes' credential pool>
  model: google/gemini-4-carbon
  response_format: json_schema
  messages: system, user
  max_tokens: 8000
```

Not proven: a real answer from Gemini 4 Carbon. The sandbox has no OpenRouter credential. Two
things only the live call can show: that the model follows the verdict schema, and what model
name OpenRouter reports back (the reviewer rejects a verdict whose model does not start with
`google/gemini-4-carbon`, to catch a provider fallback).

## Findings that change the plan

1. `granted_capabilities` in `config.yaml` alone does not open the `ctx.llm` override. Hermes
   enforces the older `plugins.entries.loopverify.llm.allow_*` keys; the consent screen writes
   both. So grant through `hermes plugins install` or `hermes plugins enable`, not by editing
   config.
2. Hermes drops `temperature` for Gemini 3.6+ models. The reviewer cannot pin sampling, so two
   reviews of the same diff may differ.
3. A `pre_tool_call` hook that raises or times out blocks the tool call. The gate hook is a pure
   function that is tested against malformed arguments, and probe recording lives in a separate
   `post_tool_call` observer, which fails open.
4. The gate hook cannot see the sandbox shell's current directory, only the command text and
   `workdir`. P1 has to decide how the gate learns which repo a `gh pr create` is for.
5. `auxiliary.free_only: true` in Hermes config would make Hermes skip OpenRouter for a paid
   model such as Gemini 4 Carbon. Worth checking if the live review fails.
6. The plan asked for `plugin.yaml` in `plugin/`. Hermes needs the manifest at the install root,
   so it sits at the repo root and `hermes plugins install TCrawley11/loopVerifyPlugin` works as
   planned.

## Host steps to finish P0

The repo is not on GitHub yet. The sandbox token may not create repositories (`gh repo create`
and `POST /user/repos` both answer HTTP 403, "Resource not accessible by personal access token"),
so the first step is on the host:

```
cd ~/code/loopVerifyPlugin
gh repo create TCrawley11/loopVerifyPlugin --public --source . --remote origin --push \
  --description "A Hermes plugin: loop with your agents as a teammate while a verification layer judges the work."

hermes auth add openrouter --api-key <key>       # skip if OpenRouter is already set up
hermes plugins install TCrawley11/loopVerifyPlugin --enable
```

Say yes to the capability prompt (`llm.provider_override`, `llm.model_override`). If `/lverify`
is not found afterwards, restart Hermes. Then, in a Hermes CLI session started in `~/code`:

1. (e, d) `/lverify p0`
2. (a) Ask the agent to run this in the terminal and report the result:
   `LVERIFY_P0_PROBE=1 gh pr create --title probe --body probe`. Expected: blocked, nothing runs.
3. (b) Ask the agent to load the skill `loopverify:p0-probe`. Expected: `LVERIFY-SKILL-OK`.
4. (c) `/lverify report`. Expected: the browser opens the evidence page.
5. (f) `/lverify review <repo> <base>` on any repo with commits ahead of `<base>`.
6. (d) From Discord: `/lverify p0`. Expected: one message, edited in place.
7. `/lverify p0` once more and paste the output here. It lists every recorded probe.

The evidence file is `~/.hermes/plugin-data/loopverify/p0-evidence.jsonl`.
