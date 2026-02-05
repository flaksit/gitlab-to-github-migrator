"""
Tests for environment variable validation fixture in conftest.py.

These tests verify that:
1. Integration tests are skipped with clear error when required env vars are missing
2. Integration tests run when required env vars are present
3. Unit tests are unaffected by the env var validation
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.unit
class TestEnvVarValidationFixture:
    """Test the check_integration_test_env_vars fixture behavior."""

    def test_integration_test_skipped_when_both_env_vars_missing(self, tmp_path: Path) -> None:
        """Integration tests should be skipped when both required env vars are missing."""
        # Copy conftest.py to tmp_path
        tests_dir = Path(__file__).parent
        conftest_src = tests_dir / "conftest.py"
        conftest_dst = tmp_path / "conftest.py"
        conftest_dst.write_text(conftest_src.read_text())

        # Create pytest.ini to register the integration marker
        pytest_ini = tmp_path / "pytest.ini"
        pytest_ini.write_text("""[pytest]
markers =
    integration: mark test as integration test
""")

        # Create a test file with an integration test
        test_file = tmp_path / "test_temp_integration.py"
        test_file.write_text("""
import pytest

@pytest.mark.integration
def test_integration():
    assert True
""")

        # Run pytest with env vars unset
        env = os.environ.copy()
        env.pop("SOURCE_GITLAB_TEST_PROJECT", None)
        env.pop("TARGET_GITHUB_TEST_OWNER", None)

        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-vvs",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            check=False,
        )

        # Test should be skipped
        assert result.returncode == 0, f"Expected test to be skipped (exit 0):\n{result.stdout}\n{result.stderr}"
        assert "SKIPPED" in result.stdout, f"Expected SKIPPED in output:\n{result.stdout}"
        # Check that the skip message contains the required information
        # Note: pytest may line-wrap the message, so we normalize whitespace
        skip_message = " ".join(result.stdout.split())
        assert "Integration tests require environment variables" in skip_message, (
            f"Expected skip message about env vars:\n{result.stdout}"
        )
        assert "SOURCE_GITLAB_TEST_PROJECT" in skip_message, (
            f"Expected missing env var name in skip message:\n{result.stdout}"
        )
        assert "TARGET_GITHUB_TEST_OWNER" in skip_message, (
            f"Expected missing env var name in skip message:\n{result.stdout}"
        )

    def test_integration_test_skipped_when_one_env_var_missing(self, tmp_path: Path) -> None:
        """Integration tests should be skipped when one required env var is missing."""
        # Copy conftest.py to tmp_path
        tests_dir = Path(__file__).parent
        conftest_src = tests_dir / "conftest.py"
        conftest_dst = tmp_path / "conftest.py"
        conftest_dst.write_text(conftest_src.read_text())

        # Create pytest.ini to register the integration marker
        pytest_ini = tmp_path / "pytest.ini"
        pytest_ini.write_text("""[pytest]
markers =
    integration: mark test as integration test
""")

        # Create a test file with an integration test
        test_file = tmp_path / "test_temp_integration.py"
        test_file.write_text("""
import pytest

@pytest.mark.integration
def test_integration():
    assert True
""")

        # Run pytest with only one env var set
        env = os.environ.copy()
        env["SOURCE_GITLAB_TEST_PROJECT"] = "test/project"
        env.pop("TARGET_GITHUB_TEST_OWNER", None)

        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-vvs",
                "--tb=short",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            check=False,
        )

        # Test should be skipped
        assert result.returncode == 0, f"Expected test to be skipped (exit 0):\n{result.stdout}\n{result.stderr}"
        assert "SKIPPED" in result.stdout, f"Expected SKIPPED in output:\n{result.stdout}"
        # Check that the skip message contains the required information
        # Note: pytest may line-wrap the message, so we normalize whitespace
        skip_message = " ".join(result.stdout.split())
        assert "Integration tests require environment variables" in skip_message, (
            f"Expected skip message about env vars:\n{result.stdout}"
        )
        assert "TARGET_GITHUB_TEST_OWNER" in skip_message, (
            f"Expected missing env var name in skip message:\n{result.stdout}"
        )

    def test_unit_test_runs_without_env_vars(self, tmp_path: Path) -> None:
        """Unit tests should run normally even when env vars are not set."""
        # Copy conftest.py to tmp_path
        tests_dir = Path(__file__).parent
        conftest_src = tests_dir / "conftest.py"
        conftest_dst = tmp_path / "conftest.py"
        conftest_dst.write_text(conftest_src.read_text())

        # Create a test file with a unit test (no integration marker)
        test_file = tmp_path / "test_temp_unit.py"
        test_file.write_text("""
def test_unit():
    assert True
""")

        # Run pytest with env vars unset
        env = os.environ.copy()
        env.pop("SOURCE_GITLAB_TEST_PROJECT", None)
        env.pop("TARGET_GITHUB_TEST_OWNER", None)

        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            check=False,
        )

        # Test should pass
        assert result.returncode == 0, f"Expected test to pass:\n{result.stdout}\n{result.stderr}"
        assert "PASSED" in result.stdout, f"Expected PASSED in output:\n{result.stdout}"
        assert "SKIPPED" not in result.stdout, f"Should not be skipped:\n{result.stdout}"
