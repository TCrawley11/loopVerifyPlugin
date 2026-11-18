import json
import subprocess
from types import SimpleNamespace

import pytest

from plugin import p0, reviewer


class FakeMessage:
    ok = True
    error = None

    def __init__(self, posts):
        self.posts = posts

    def edit(self, text):
        self.posts.append(text)
        return {"ok": True, "message_id": "m1", "edited": True}


class FakeLlm:
    def __init__(self):
        self.parsed = {"summary": "Reviewed.", "findings": []}
        self.text = '{"summary": "Reviewed.", "findings": []}'
        self.error = None
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300)
        return SimpleNamespace(
            provider=reviewer.PROVIDER,
            model=reviewer.MODEL,
            parsed=self.parsed,
            text=self.text,
            usage=usage,
        )


def verify_output(repo_dir, passed=True, clean=True):
    sha = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    result = {"name": "lint", "error": None, "new": [] if passed else [{"code": "F401"}]}
    report = {"sha": sha, "clean": clean, "passed": passed, "results": [result]}
    return "Installed 3 packages\n" + json.dumps(report, indent=2)


class FakeEnv:
    """A local-backend terminal environment: sandbox paths are host paths."""

    def __init__(self, cwd):
        self.cwd = str(cwd)


class FakeCtx:
    """The slice of Hermes' plugin context that loopverify uses."""

    def __init__(self, cwd, surface="cli"):
        self.cwd = str(cwd)
        self.surface = surface
        self.llm = FakeLlm()
        self.posts = []
        self.dispatched = []

    verify_passes = True
    verify_clean = True

    def get_config(self, key, default=None):
        return default

    def dispatch_tool(self, name, args, task_id=None, **kwargs):
        assert name == "terminal"
        self.dispatched.append({**args, "task_id": task_id})
        output = verify_output(args["workdir"], self.verify_passes, self.verify_clean)
        return json.dumps({"output": output, "exit_code": 0 if self.verify_passes else 1})

    def session_cwd(self):
        return self.cwd

    def session_surface(self):
        return self.surface

    def post_message(self, text):
        self.posts.append(text)
        return FakeMessage(self.posts)

    def redact_text(self, text, *, force=False):
        assert force, "review input leaves the machine, so redaction must be forced"
        return text.replace("sk-secret", "REDACTED")


@pytest.fixture
def ctx(tmp_path):
    return FakeCtx(tmp_path)


@pytest.fixture
def log(tmp_path):
    return p0.EvidenceLog(tmp_path / "data" / "p0-evidence.jsonl")
