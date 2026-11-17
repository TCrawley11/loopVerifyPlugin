import pytest

from loopverify import state
from loopverify.errors import SetupError

from .conftest import KEY, StateRemote, git


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Owner/proj.git",
        "https://github.com/Owner/proj",
        "https://x-access-token@github.com/Owner/proj.git/",
        "git@github.com:Owner/proj.git",
        "ssh://git@github.com:22/Owner/proj.git",
        "https://GitHub.com/Owner/proj",
    ],
)
def test_every_remote_url_form_maps_to_host_owner_repo(url):
    assert state.project_key(url) == KEY


@pytest.mark.parametrize("url", ["", "not a url", "https://github.com/", "git@h:../../etc"])
def test_an_unusable_remote_url_is_a_setup_error(url):
    with pytest.raises(SetupError):
        state.project_key(url)


def test_without_a_state_repo_the_error_says_how_to_connect_one(project):
    git(project, "config", "--unset", "lverify.stateRepo")
    with pytest.raises(SetupError, match="lverify init --state-repo"):
        state.open_state(project)


def test_first_use_clones_only_this_projects_folder_into_the_git_dir(project, remote):
    remote.set_config('base = "main"\n')
    opened = state.open_state(project)
    assert opened.refreshed
    assert opened.clone == project / ".git" / "lverify" / "state"
    assert opened.config_file.read_text() == 'base = "main"\n'
    assert not (opened.clone / "github.com" / "Other").exists()
    assert git(project, "status", "--porcelain") == ""


def test_pointing_at_another_state_repo_replaces_the_cached_clone(project, remote, tmp_path):
    remote.set_config('base = "main"\n')
    state.open_state(project)
    (tmp_path / "other").mkdir()
    other = StateRemote(tmp_path / "other")
    other.set_config('base = "dev"\n')
    git(project, "config", "lverify.stateRepo", other.url)
    opened = state.open_state(project)
    assert opened.refreshed
    assert opened.config_file.read_text() == 'base = "dev"\n'


def test_a_new_origin_url_checks_out_the_new_projects_folder(project, remote):
    remote.set_config('base = "main"\n')
    remote.set_config('base = "dev"\n', key="github.com/Owner/renamed")
    state.open_state(project)
    git(project, "remote", "set-url", "origin", "https://github.com/Owner/renamed.git")
    opened = state.open_state(project)
    assert opened.key == "github.com/Owner/renamed"
    assert opened.config_file.read_text() == 'base = "dev"\n'


def test_within_the_ttl_the_state_repo_is_not_contacted(project, remote):
    remote.set_config('base = "main"\n')
    state.open_state(project, now=1_000_000)
    remote.bare.rename(remote.bare.with_suffix(".gone"))
    opened = state.open_state(project, now=1_000_000 + state.FETCH_TTL_SECONDS - 1)
    assert (opened.refreshed, opened.warning) == (False, None)
    assert opened.config_file.exists()


def test_after_the_ttl_a_change_in_the_state_repo_is_picked_up(project, remote):
    remote.set_config('base = "main"\n')
    first = state.open_state(project)
    remote.set_config('base = "dev"\n')
    marker = first.clone.parent / "state-fetched"
    later = marker.stat().st_mtime + state.FETCH_TTL_SECONDS
    opened = state.open_state(project, now=later)
    assert opened.refreshed
    assert opened.config_file.read_text() == 'base = "dev"\n'


def test_force_refresh_ignores_the_ttl(project, remote):
    remote.set_config('base = "main"\n')
    state.open_state(project)
    remote.set_config('base = "dev"\n')
    assert state.open_state(project, force_refresh=True).config_file.read_text() == 'base = "dev"\n'


def test_offline_after_the_ttl_uses_the_cache_and_waits_a_full_ttl_to_retry(project, remote):
    remote.set_config('base = "main"\n')
    first = state.open_state(project)
    remote.bare.rename(remote.bare.with_suffix(".gone"))
    marker = first.clone.parent / "state-fetched"
    later = marker.stat().st_mtime + state.FETCH_TTL_SECONDS
    offline = state.open_state(project, now=later)
    assert not offline.refreshed
    assert "using the cached copy" in offline.warning
    assert offline.config_file.read_text() == 'base = "main"\n'
    retried = state.open_state(project, now=later + 1)
    assert (retried.refreshed, retried.warning) == (False, None)
