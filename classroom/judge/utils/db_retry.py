"""Retry a whole write when InnoDB picks it as a deadlock victim.

Under many concurrent teachers InnoDB occasionally answers a write with error 1213
("Deadlock found when trying to get lock; try restarting transaction"). The
classic case here: two requests whose INSERT hit the same unique name/code both
keep a shared lock on that index record after their savepoint rolls back, and
both then INSERT the next free value into the same gap. MySQL rolls the whole
transaction back and expects the client to run it again, so that is what this
does: the decorated callable (which owns its outermost transaction) is called
again, up to ``attempts`` times, after a short random pause.

Only the outermost caller may retry. Inside an atomic block the transaction is
already gone, so the error propagates to whoever opened it.
"""
import functools
import logging
import random
import time

from django.db import OperationalError, connection

logger = logging.getLogger('judge.db_retry')

MYSQL_DEADLOCK = 1213
DEADLOCK_ATTEMPTS = 4


def is_deadlock(exc):
    return isinstance(exc, OperationalError) and bool(exc.args) and exc.args[0] == MYSQL_DEADLOCK


def retry_on_deadlock(func=None, *, attempts=DEADLOCK_ATTEMPTS, before_retry=None):
    """Decorator: run ``func`` again when its transaction was rolled back as a deadlock victim.

    ``before_retry(*args, **kwargs)`` (same arguments as ``func``) can restore in-memory state
    the failed attempt changed (e.g. a primary key set by an INSERT that was rolled back).
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except OperationalError as exc:
                    if not is_deadlock(exc) or connection.in_atomic_block or attempt == attempts - 1:
                        raise
                    logger.warning('Deadlock in %s, retrying (attempt %d)',
                                   getattr(fn, '__qualname__', fn), attempt + 2)
                    time.sleep(random.uniform(0.01, 0.05) * (attempt + 1))
                    if before_retry is not None:
                        before_retry(*args, **kwargs)
        return wrapper

    return decorate(func) if func is not None else decorate
