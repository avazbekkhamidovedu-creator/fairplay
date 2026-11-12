"""Per-student FairPlay mode for the teacher tables of a FairPlay exam, from the batch exam response."""
from django.utils.translation import gettext_lazy as _l

__all__ = ['mode_table_from_exam']


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


def mode_table_from_exam(exam):
    """The mode table for an exam answer the caller already has (None when there is none)."""
    return _build(exam) if isinstance(exam, dict) and exam else None
