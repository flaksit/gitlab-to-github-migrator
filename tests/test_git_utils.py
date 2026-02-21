"""Tests for git utility functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gitlab_to_github_migrator.git_utils import (
    UpdatedRemote,
    _build_github_url,
    _get_backup_remote_name,
    _matches_gitlab_project,
    update_remotes_after_migration,
)


@pytest.mark.unit
class TestMatchesGitLabProject:
    """Tests for _matches_gitlab_project()."""

    def test_https_url_matches(self) -> None:
        assert _matches_gitlab_project("https://gitlab.com/ns/repo.git", "ns/repo")

    def test_https_url_matches_without_git_suffix(self) -> None:
        assert _matches_gitlab_project("https://gitlab.com/ns/repo", "ns/repo")

    def test_ssh_url_matches(self) -> None:
        assert _matches_gitlab_project("git@gitlab.com:ns/repo.git", "ns/repo")

    def test_ssh_url_matches_without_git_suffix(self) -> None:
        assert _matches_gitlab_project("git@gitlab.com:ns/repo", "ns/repo")

    def test_custom_gitlab_instance_https(self) -> None:
        assert _matches_gitlab_project("https://git.example.com/ns/repo.git", "ns/repo")

    def test_custom_gitlab_instance_ssh(self) -> None:
        assert _matches_gitlab_project("git@git.example.com:ns/repo.git", "ns/repo")

    def test_different_project_does_not_match(self) -> None:
        assert not _matches_gitlab_project("https://gitlab.com/ns/other.git", "ns/repo")

    def test_different_namespace_does_not_match(self) -> None:
        assert not _matches_gitlab_project("https://gitlab.com/other/repo.git", "ns/repo")

    def test_partial_name_does_not_match(self) -> None:
        # "ns/repo" should not match "ns/repo-extra"
        assert not _matches_gitlab_project("https://gitlab.com/ns/repo-extra.git", "ns/repo")

    def test_trailing_slash_in_path_is_ignored(self) -> None:
        assert _matches_gitlab_project("https://gitlab.com/ns/repo.git", "ns/repo/")


@pytest.mark.unit
class TestBuildGithubUrl:
    """Tests for _build_github_url()."""

    def test_https_original_produces_https(self) -> None:
        result = _build_github_url("https://gitlab.com/ns/repo.git", "owner/repo")
        assert result == "https://github.com/owner/repo.git"

    def test_ssh_original_produces_ssh(self) -> None:
        result = _build_github_url("git@gitlab.com:ns/repo.git", "owner/repo")
        assert result == "git@github.com:owner/repo.git"

    def test_ssh_scheme_url_produces_ssh(self) -> None:
        result = _build_github_url("ssh://git@gitlab.com/ns/repo.git", "owner/repo")
        assert result == "git@github.com:owner/repo.git"


@pytest.mark.unit
class TestGetBackupRemoteName:
    """Tests for _get_backup_remote_name()."""

    def test_origin_becomes_gitlab(self) -> None:
        assert _get_backup_remote_name("origin") == "gitlab"

    def test_other_name_gets_gitlab_suffix(self) -> None:
        assert _get_backup_remote_name("upstream") == "upstream-gitlab"

    def test_custom_name(self) -> None:
        assert _get_backup_remote_name("my-remote") == "my-remote-gitlab"

    def test_already_gitlab_name(self) -> None:
        assert _get_backup_remote_name("gitlab") == "gitlab-gitlab"


@pytest.mark.unit
class TestUpdateRemotesAfterMigration:
    """Tests for update_remotes_after_migration()."""

    def _make_run(self, remotes_stdout: str, *, is_git_repo: bool = True) -> MagicMock:
        """Return a mock for subprocess.run with pre-configured side effects."""
        mock = MagicMock()

        def side_effect(cmd: list[str], **_kwargs: object) -> MagicMock:
            result = MagicMock()
            if cmd[1:3] == ["rev-parse", "--git-dir"]:
                result.returncode = 0 if is_git_repo else 1
            elif cmd[1:3] == ["remote", "-v"]:
                result.returncode = 0
                result.stdout = remotes_stdout
            else:
                result.returncode = 0
            return result

        mock.side_effect = side_effect
        return mock

    def test_not_in_git_repo_returns_empty(self) -> None:
        with patch("gitlab_to_github_migrator.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = update_remotes_after_migration("ns/repo", "owner/newrepo")
        assert result == []

    def test_no_matching_remote_returns_empty(self) -> None:
        remotes = "origin\thttps://github.com/someone/other.git (fetch)\n"
        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", self._make_run(remotes)):
            result = update_remotes_after_migration("ns/repo", "owner/newrepo")
        assert result == []

    def test_origin_remote_https_is_updated(self) -> None:
        remotes = "origin\thttps://gitlab.com/ns/repo.git (fetch)\n"
        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", self._make_run(remotes)):
            result = update_remotes_after_migration("ns/repo", "owner/newrepo")

        assert result == [
            UpdatedRemote(
                remote_name="origin",
                old_url="https://gitlab.com/ns/repo.git",
                backup_name="gitlab",
                new_url="https://github.com/owner/newrepo.git",
            )
        ]

    def test_origin_remote_ssh_is_updated_as_ssh(self) -> None:
        remotes = "origin\tgit@gitlab.com:ns/repo.git (fetch)\n"
        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", self._make_run(remotes)):
            result = update_remotes_after_migration("ns/repo", "owner/newrepo")

        assert len(result) == 1
        assert result[0].new_url == "git@github.com:owner/newrepo.git"

    def test_non_origin_remote_gets_gitlab_suffix(self) -> None:
        remotes = "upstream\thttps://gitlab.com/ns/repo.git (fetch)\n"
        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", self._make_run(remotes)):
            result = update_remotes_after_migration("ns/repo", "owner/newrepo")

        assert len(result) == 1
        assert result[0].backup_name == "upstream-gitlab"

    def test_backup_remote_add_called_with_old_url(self) -> None:
        remotes = "origin\thttps://gitlab.com/ns/repo.git (fetch)\n"
        calls_made: list[list[str]] = []

        def side_effect(cmd: list[str], **_kwargs: object) -> MagicMock:
            calls_made.append(list(cmd))
            return MagicMock(returncode=0, stdout=remotes if cmd[1:3] == ["remote", "-v"] else "")

        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", side_effect=side_effect):
            update_remotes_after_migration("ns/repo", "owner/newrepo")

        # The "remote add" call should use the old GitLab URL
        add_calls = [c for c in calls_made if c[1:3] == ["remote", "add"]]
        assert len(add_calls) == 1
        assert add_calls[0] == ["git", "remote", "add", "gitlab", "https://gitlab.com/ns/repo.git"]

        # The "remote set-url" call should use the new GitHub URL
        set_url_calls = [c for c in calls_made if c[1:3] == ["remote", "set-url"]]
        assert len(set_url_calls) == 1
        assert set_url_calls[0] == [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://github.com/owner/newrepo.git",
        ]

    def test_cwd_is_passed_to_subprocess(self) -> None:
        remotes = "origin\thttps://gitlab.com/ns/repo.git (fetch)\n"
        cwd_used: list[str | None] = []

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            cwd_used.append(str(kwargs.get("cwd")))
            return MagicMock(returncode=0, stdout=remotes if cmd[1:3] == ["remote", "-v"] else "")

        with patch("gitlab_to_github_migrator.git_utils.subprocess.run", side_effect=side_effect):
            update_remotes_after_migration("ns/repo", "owner/newrepo", cwd="/some/path")

        assert all(c == "/some/path" for c in cwd_used)
