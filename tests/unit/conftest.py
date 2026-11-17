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
        self.error = None
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(provider=reviewer.PROVIDER, model=reviewer.MODEL, parsed=self.parsed)


class FakeCtx:
    """The slice of Hermes' plugin context that loopverify uses."""

    def __init__(self, cwd, surface="cli"):
        self.cwd = str(cwd)
        self.surface = surface
        self.llm = FakeLlm()
        self.posts = []

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
