import subprocess

import pytest


@pytest.fixture
def repo_with_change(tmp_path):
    """A git repo whose HEAD is one commit ahead of `main`."""
    path = tmp_path / "repo"
    path.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.com")
    (path / "x.py").write_text("def add(a, b):\n    return a + b\n")
    git("add", ".")
    git("commit", "-m", "base")
    git("checkout", "-b", "feat")
    (path / "x.py").write_text("def add(a, b):\n    return a - b\n")
    git("commit", "-am", "change")
    return path
