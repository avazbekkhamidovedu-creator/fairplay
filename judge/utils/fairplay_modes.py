"""Per-student FairPlay mode for the teacher tables of a FairPlay exam, from the batch exam response."""
import logging

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _l

from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT

__all__ = ['MODES_CACHE_SECONDS', 'fairplay_mode_table']

logger = logging.getLogger('judge.fairplay')

MODES_CACHE_KEY = 'fpb:modes:%s'
MODES_CACHE_SECONDS = 5

MODE_LABELS = {
    'on': _l('ON'), 'off': _l('OFF'), 'not_started': _l('NOT STARTED'), 'submitted': _l('SUBMITTED'),
}
DEVICE_LABELS = {'unused': _l('unused'), 'bound': _l('bound')}
# Older FairPlay sends only `status`.
STATUS_TO_MODE = {
    'not_started': 'not_started', 'writing': 'on', 'offline': 'off', 'waiting': 'off', 'handed_in': 'submitted',
}


def _attempts(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _cell(student):
    mode = student.get('fp_mode')
    if mode not in MODE_LABELS:
        mode = None
    device = student.get('device_state')
    if device not in DEVICE_LABELS:
        device = None
    return {
        'mode': mode, 'mode_label': MODE_LABELS.get(mode),
        'device': device, 'device_label': DEVICE_LABELS.get(device),
        'second_device': _attempts(student.get('second_device_attempts')),
        'last_beat_at': student.get('last_beat_at') if isinstance(student.get('last_beat_at'), str) else None,
    }


def _build(exam):
    settings = exam.get('settings') if isinstance(exam.get('settings'), dict) else {}
    students = {}
    for s in exam.get('students') or []:
        if isinstance(s, dict) and s.get('netid'):
            students[str(s['netid'])] = _cell(s)
    modes = [c['mode'] for c in students.values()]
    return {
        'synced': bool(exam.get('synced', True)),
        'status': exam.get('status'),
        'strong_lock': settings.get('strong_lock') if isinstance(settings.get('strong_lock'), bool) else None,
        'students': students,
        'counts': {m: modes.count(m) for m in MODE_LABELS},
        'second_device_total': sum(c['second_device'] for c in students.values()),
        'second_device_students': sum(1 for c in students.values() if c['second_device']),
    }


def fairplay_mode_table(contest, acting_username):
    """None when FairPlay cannot be reached; callers then show '-' everywhere."""
    key = MODES_CACHE_KEY % contest.key
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        exam = FairPlayClient(acting_user=acting_username).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
    except FairPlayError:
        return None
    if not isinstance(exam, dict):
        return None
    table = _build(exam)
    cache.set(key, table, MODES_CACHE_SECONDS)
    return table
