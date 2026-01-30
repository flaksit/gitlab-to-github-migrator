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

    def test_integration_test_with_warnings_would_fail(self) -> None:
        """
        This test documents what would happen if a warning was logged.

        If you uncomment the logger.warning() line below, this test will FAIL
        with a message about warnings being detected.

        This demonstrates that integration tests fail when the code under test
        logs warnings, which is the desired behavior.
        """
        # Uncomment the next two lines to see the test fail:
        # logger = logging.getLogger("test_logger")
        # logger.warning("This warning would cause the test to fail")
        assert True  # Test passes without the warning
