# loopVerifyPlugin

loopVerifyPlugin is a Hermes plugin. It lets you "loop" with the agent(s) and work as a
teammate, while a verification layer acts as the judge of the work.

Status: **P0, proving the platform.** The verify CLI, the real PR gate and the chunk flow are not
built yet. The plan is in [PLAN.md](PLAN.md); what is proven so far is in
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
uv run pytest                                    # unit tests
uv run ruff check . && uv run ruff format --check .
```

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
