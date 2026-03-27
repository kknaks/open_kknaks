"""Tests for exception hierarchy."""

from open_kknaks.exceptions import (
    BillingError,
    ClaudeAuthError,
    ClaudeNotFoundError,
    ExecutionError,
    IdleTimeoutError,
    OpenKknaksError,
    RateLimitError,
    TaskCancelledError,
    TaskTimeoutError,
)


def test_all_exceptions_inherit_from_base() -> None:
    exceptions = [
        ClaudeAuthError,
        ClaudeNotFoundError,
        BillingError,
        TaskCancelledError,
        TaskTimeoutError,
        IdleTimeoutError,
        RateLimitError,
        ExecutionError,
    ]
    for exc_cls in exceptions:
        assert issubclass(exc_cls, OpenKknaksError)
        assert issubclass(exc_cls, Exception)


def test_exception_with_message() -> None:
    err = BillingError("billing limit exceeded")
    assert str(err) == "billing limit exceeded"
    assert isinstance(err, OpenKknaksError)


def test_exception_type_name() -> None:
    err = ClaudeAuthError("auth failed")
    assert type(err).__name__ == "ClaudeAuthError"


def test_exception_catch_by_base() -> None:
    try:
        raise TaskTimeoutError("timed out")
    except OpenKknaksError as e:
        assert str(e) == "timed out"
