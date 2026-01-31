"""Tests for git migration functions."""

import pytest

from gitlab_to_github_migrator.git_migration import _inject_token, _sanitize_error


@pytest.mark.unit
class TestInjectToken:
    def test_inject_gitlab_token(self) -> None:
        url = "https://gitlab.com/org/repo.git"
        result = _inject_token(url, "my_token", prefix="oauth2:")
        assert result == "https://oauth2:my_token@gitlab.com/org/repo.git"

    def test_inject_github_token(self) -> None:
        url = "https://github.com/org/repo.git"
        result = _inject_token(url, "gh_token", prefix="")
        assert result == "https://gh_token@github.com/org/repo.git"

    def test_no_token_returns_original(self) -> None:
        url = "https://gitlab.com/org/repo.git"
        result = _inject_token(url, None, prefix="oauth2:")
        assert result == url

    def test_non_https_returns_original(self) -> None:
        url = "git@gitlab.com:org/repo.git"
        result = _inject_token(url, "token", prefix="oauth2:")
        assert result == url


@pytest.mark.unit
class TestSanitizeError:
    def test_sanitize_single_token(self) -> None:
        error = "Failed to clone https://oauth2:secret123@gitlab.com/repo"
        result = _sanitize_error(error, ["secret123"])
        assert "secret123" not in result
        assert "***TOKEN***" in result

    def test_sanitize_multiple_tokens(self) -> None:
        error = "Error: token1 and token2 exposed"
        result = _sanitize_error(error, ["token1", "token2"])
        assert "token1" not in result
        assert "token2" not in result

    def test_sanitize_with_none_tokens(self) -> None:
        error = "Some error message"
        result = _sanitize_error(error, [None, "token"])
        assert result == "Some error message".replace("token", "***TOKEN***")

    def test_empty_tokens_list(self) -> None:
        error = "Some error"
        result = _sanitize_error(error, [])
        assert result == "Some error"
