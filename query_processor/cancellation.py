"""Sinyal pembatalan lokal untuk eksekusi satu request."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


CancelCheck = Callable[[], bool]
_cancel_check: ContextVar[CancelCheck | None] = ContextVar("qp_cancel_check", default=None)


def current_cancel_check() -> CancelCheck | None:
    return _cancel_check.get()


@contextmanager
def cancellation_scope(check: CancelCheck) -> Iterator[None]:
    token = _cancel_check.set(check)
    try:
        yield
    finally:
        _cancel_check.reset(token)
