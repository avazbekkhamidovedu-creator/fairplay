"""Request-local access scope, installed only after verified device admission.

Never store this scope on a User, in a browser session, or in a shared cache.
ContextVar reset is mandatory so a later ordinary browser request cannot inherit it.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_scope = ContextVar('fairplay_native_admission', default=None)


def admitted_contest_id(user):
    scope = _scope.get()
    if scope is not None and user.is_authenticated and user.pk == scope[0]:
        return scope[1]
    return None


@contextmanager
def device_scope(session):
    token = _scope.set((session.user_id, session.contest_id))
    try:
        yield
    finally:
        _scope.reset(token)
