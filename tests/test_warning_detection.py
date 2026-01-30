"""
Tests to demonstrate and verify conftest.py warning detection functionality.

These tests verify that:
1. Integration tests fail when logger.warning() is called
2. Unit tests allow logger.warning() without failing
"""

import logging

import pytest


@pytest.mark.unit
class TestUnitTestWarningBehavior:
    """Verify that unit tests allow warnings without failing."""

    def test_unit_test_allows_logger_warnings(self) -> None:
        """Unit tests should allow logger warnings without failing the test."""
        logger = logging.getLogger("test_logger")
        logger.warning("This is a warning in a unit test - should not fail")
        assert True  # Test passes despite the warning


@pytest.mark.integration
class TestIntegrationTestWarningBehavior:
    """Verify that integration tests fail when warnings are detected."""

    def test_integration_test_without_warnings_passes(self) -> None:
        """Integration test with no warnings should pass normally."""
        logger = logging.getLogger("test_logger")
        logger.info("This is just an info log - should not fail")
        logger.debug("This is a debug log - should not fail")
        assert True  # Test passes - no warnings

    def test_integration_test_with_warning_fails(self, tmp_path) -> None:
        """Verify that integration tests fail when logger.warning() is called.

        This test creates a temporary test file that emits a warning,
        runs pytest on it, and verifies that it fails with the expected error message.
        """
        import shutil
        import subprocess
        import sys
        from pathlib import Path

        # Copy conftest.py to tmp_path so it's picked up
        tests_dir = Path(__file__).parent
        shutil.copy(tests_dir / "conftest.py", tmp_path / "conftest.py")

        # Create a test file that emits a warning
        test_file = tmp_path / "test_temp_warning.py"
        test_file.write_text("""
import logging
import pytest

@pytest.mark.integration
def test_warning():
    logger = logging.getLogger("test_logger")
    logger.warning("Test warning message")
""")

        # Run pytest on the test file
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=False,
        )

        # The test should fail due to the warning
        assert result.returncode != 0, f"Expected test to fail but it passed:\n{result.stdout}"
        assert "warning(s) detected" in result.stdout or "warning(s) detected" in result.stderr, (
            f"Expected 'warning(s) detected' in output:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
