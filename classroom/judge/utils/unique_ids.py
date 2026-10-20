"""Next unused ``p######`` / ``c######`` ids from one in-memory pass.

Callers load existing values once (one query), then this finds the first hole
in the zero-padded series. That is the same numbering as the old one-by-one
``exists()`` / HTTP checks, without a round trip per candidate.
"""
import random
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

SERIES_LIMIT = 1000000
# How many INSERTs ``save_with_unique_id`` tries before giving up. Each retry after
# the first spreads over more free slots, so a crowd of teachers saving at the same
# second does not keep fighting over the same "first free" id.
UNIQUE_ID_ATTEMPTS = 10


class UniqueIdTaken(Exception):
    """The id ``value`` was taken by another writer and the caller may not pick another."""

    def __init__(self, value):
        super().__init__(value)
        self.value = value


def next_prefixed_id(existing_values, prefix, width=6, fallback_prefix=None, spread=0):
    """Return ``prefix + zero-padded index`` for the first unused slot.

    ``spread > 0`` returns a random one of the first ``spread * 4`` free slots instead
    of the very first: used when retrying after losing a race for the first one.
    """
    existing = existing_values if isinstance(existing_values, set) else set(existing_values)
    skip = random.randrange(spread * 4) if spread > 0 else 0
    for index in range(SERIES_LIMIT):
        candidate = '%s%0*d' % (prefix, width, index)
        if candidate not in existing:
            if not skip:
                return candidate
            skip -= 1
    stamp = str(int(timezone.now().timestamp()))[-width:]
    return '%s%s' % (fallback_prefix or prefix, stamp)


def save_with_unique_id(instance, field, generate, *, regenerate=True, attempts=UNIQUE_ID_ATTEMPTS):
    """INSERT ``instance``; if another writer took its ``field`` value first, pick another.

    Checking that an id is free and inserting it are two steps, so two teachers who
    save at the same moment (or who opened the form together and got the same
    suggested id) both pass the check and one INSERT hits the unique index. Each
    attempt runs in a savepoint, so a surrounding transaction stays usable after a
    collision. ``generate(attempt)`` returns the next candidate (attempt >= 1).

    With ``regenerate=False`` (an id the teacher typed) a collision raises
    ``UniqueIdTaken`` for the caller to report; other integrity errors propagate.
    ``regenerate`` may also be a callable ``(taken_value) -> bool`` deciding per
    collision (it may raise to abort, e.g. "this is your own earlier save").
    An instance that already has a primary key is UPDATEd instead (a rename).
    """
    model = type(instance)
    inserting = instance.pk is None
    for attempt in range(attempts):
        try:
            with transaction.atomic():
                instance.save(force_insert=inserting)
            return instance
        except IntegrityError:
            if inserting:
                instance.pk = None
            value = getattr(instance, field)
            others = model._default_manager.filter(**{field: value})
            if not inserting:
                others = others.exclude(pk=instance.pk)
            if not others.exists():
                raise
            allowed = regenerate(value) if callable(regenerate) else regenerate
            if not allowed or attempt == attempts - 1:
                raise UniqueIdTaken(value)
            setattr(instance, field, generate(attempt + 1))
    raise UniqueIdTaken(getattr(instance, field))


def is_series_id(value, prefix, width=6):
    """True when ``value`` is in the auto-suggested ``p000000`` / ``c000000`` series."""
    if not value:
        return False
    return re.fullmatch('%s[0-9]{%d}' % (re.escape(prefix), width), value) is not None


def wants_auto_series(flag, value, prefix, width=6):
    """Whether a colliding id should be replaced with the next free series slot.

    ``flag`` is the client ``key_auto`` / ``code_auto`` field. Missing flags
    fall back to the id looking like an auto-suggested series value.
    """
    if flag is None:
        return is_series_id(value, prefix, width)
    if flag is True or flag == 1:
        return True
    if flag is False or flag == 0:
        return False
    text = str(flag).strip().lower()
    if text in ('1', 'true', 'yes'):
        return True
    if text in ('0', 'false', 'no', ''):
        return False
    return is_series_id(value, prefix, width)
