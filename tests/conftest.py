"""
Pytest configuration and fixtures.

This module configures pytest behavior for different test types:
- Integration tests: Fail on any warnings from the code under test
- Unit tests: Allow warnings (for backwards compatibility)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, override

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# Store warning records during test execution
_integration_test_warnings: dict[str, list[logging.LogRecord]] = {}


class IntegrationTestWarningHandler(logging.Handler):
    """Custom logging handler to capture warnings during integration tests."""

    test_nodeid: str

    def __init__(self, test_nodeid: str) -> None:
        super().__init__()
        self.test_nodeid = test_nodeid
        self.setLevel(logging.WARNING)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Capture WARNING and above level logs."""
        if self.test_nodeid not in _integration_test_warnings:
            _integration_test_warnings[self.test_nodeid] = []
        _integration_test_warnings[self.test_nodeid].append(record)


@pytest.fixture(autouse=True)
def fail_on_log_warnings_for_integration_tests(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """
    Automatically fail integration tests if any WARNING level logs are emitted from the code under test.

    This fixture captures logging output and fails the test if any WARNING or ERROR
    level logs are detected during integration tests. These would come from logger.warning()
    or logger.error() calls in the source code.

    Warnings from the test code itself (via warnings.warn()) are allowed, as they are
    just informational output. This fixture specifically targets logger warnings which
    indicate issues in the code under test.

    Warnings are acceptable when running the tool as a user, but in the test context
    we don't expect any warnings from the migrator code and treat them as test failures.
    """
    # Check if this is an integration test
    is_integration_test = request.node.get_closest_marker("integration") is not None

    if not is_integration_test:
        # For unit tests and other tests, don't check for warnings
        yield
        return

    # For integration tests, set up warning capture
    test_nodeid = request.node.nodeid
    _integration_test_warnings[test_nodeid] = []

    # Add handler to root logger to capture all warnings
    handler = IntegrationTestWarningHandler(test_nodeid)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    try:
        yield
    finally:
        # Clean up - remove the handler
        root_logger.removeHandler(handler)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None]:  # type: ignore[misc]
    """
    Hook to check for warnings after test execution and mark test as failed if warnings were detected.

    This runs after the test completes but before pytest generates the final report.
    """
    # Execute the test and get the report
    outcome = yield
    report = outcome.get_result()

    # Only check during the test call phase (not setup or teardown)
    if call.when == "call" and report.outcome == "passed":
        # Check if this test has any captured warnings
        test_nodeid = item.nodeid
        warning_records = _integration_test_warnings.get(test_nodeid, [])

        if warning_records:
            # Format warning messages for better readability
            warning_messages = [
                f"{record.levelname}: {record.getMessage()} (in {record.name}:{record.lineno})"
                for record in warning_records
            ]

            # Mark the test as failed
            report.outcome = "failed"
            report.longrepr = f"Integration test failed: {len(warning_records)} warning(s) detected:\n" + "\n".join(
                f"  - {msg}" for msg in warning_messages
            )

        # Clean up the warnings for this test
        _integration_test_warnings.pop(test_nodeid, None)
