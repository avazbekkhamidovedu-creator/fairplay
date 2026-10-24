"""FairPlay in the assignment editor and on the assignment page; the exam settings are stored in FairPlay."""
import logging

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from judge.utils.fairplay import DELIVERY_WINDOW, FAIRPLAY_CALCULATOR_CHOICES, FAIRPLAY_LEAD, \
    FAIRPLAY_REVIEW_CHOICES, FAIRPLAY_REVIEW_HELP, fairplay_check_contest, fairplay_edit_locked, fairplay_enabled, \
    fairplay_shared_problems, fairplay_window_start, is_fairplay_exam, set_fairplay_exam

__all__ = [
    'DEFAULT_SETTINGS', 'FAIRPLAY_EDITOR_FIELDS', 'FairPlayEditLock', 'FairPlayEditorPlan', 'FairPlaySaveResult',
    'apply_fairplay_editor', 'clean_settings', 'editor_settings', 'fairplay_check_edit', 'fairplay_contest_context',
    'fairplay_edit_lock', 'fairplay_editor_context', 'fairplay_save_settings', 'fairplay_shared_warning',
    'parse_fairplay_payload', 'pop_unsaved_settings', 'push_settings', 'stash_unsaved_settings', 'websites_text',
]

logger = logging.getLogger('judge.fairplay')

# Editor fields gated by the same permission as the Safe Exam Browser option (contest_seb_requirement).
FAIRPLAY_EDITOR_FIELDS = ('fairplay_required', 'fairplay_settings')
# What FairPlay uses until settings are saved.
DEFAULT_SETTINGS = {
    'calculator': 'off', 'open_book': False, 'websites': [], 'strong_lock': False,
    'resume_needs_approval': True, 'upload_opens_days': 0, 'review': 'nothing',
}
MAX_WEBSITE_LINES = 100
MAX_WEBSITE_LENGTH = 2100
MAX_UPLOAD_DAYS = 7
UNSAVED_SESSION_KEY = 'fairplay_unsaved_settings'


def _bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'on', 'yes')
    return bool(value)


def clean_settings(raw, partial=False):
    """The body for FairPlay; the website lines are parsed (and refused) by FairPlay itself."""
    if not isinstance(raw, dict):
        raise ValidationError(_('Invalid FairPlay settings.'))
    out = {}
    if 'calculator' in raw or not partial:
        value = str(raw.get('calculator') or 'off')
        if value not in dict(FAIRPLAY_CALCULATOR_CHOICES):
            raise ValidationError(_('Invalid FairPlay calculator.'))
        out['calculator'] = value
    for field in ('open_book', 'strong_lock', 'resume_needs_approval'):
        if field in raw or not partial:
            out[field] = _bool(raw.get(field, DEFAULT_SETTINGS[field]))
    if 'websites' in raw or not partial:
        websites = raw.get('websites') or []
        if isinstance(websites, str):
            websites = websites.splitlines()
        if not isinstance(websites, list) or not all(isinstance(line, str) for line in websites):
            raise ValidationError(_('Allowed websites: one site or link per line.'))
        lines = [line.strip() for line in websites if line.strip()]
        if len(lines) > MAX_WEBSITE_LINES:
            raise ValidationError(_('Allowed websites: at most %d lines.') % MAX_WEBSITE_LINES)
        if any(len(line) > MAX_WEBSITE_LENGTH for line in lines):
            raise ValidationError(_('Allowed websites: a line is too long.'))
        out['websites'] = lines
    if 'upload_opens_days' in raw or not partial:
        try:
            days = int(raw.get('upload_opens_days') or 0)
        except (TypeError, ValueError):
            raise ValidationError(_('Early upload: a whole number of days from 0 to %d.') % MAX_UPLOAD_DAYS)
        if not 0 <= days <= MAX_UPLOAD_DAYS:
            raise ValidationError(_('Early upload: a whole number of days from 0 to %d.') % MAX_UPLOAD_DAYS)
        out['upload_opens_days'] = days
    if 'review' in raw or not partial:
        value = str(raw.get('review') or 'nothing')
        if value not in dict(FAIRPLAY_REVIEW_CHOICES):
            raise ValidationError(_('Invalid FairPlay student review setting.'))
        out['review'] = value
    return out


def websites_text(settings):
    """The allowed websites as the professor typed them, one per line (from FairPlay's answer)."""
    settings = settings or {}
    typed = settings.get('websites_input')
    if isinstance(typed, list):
        return '\n'.join(str(line) for line in typed if isinstance(line, str))
    lines, linked = [], set()
    for link in settings.get('website_links') or []:
        if isinstance(link, dict) and isinstance(link.get('url'), str):
            title = str(link.get('title') or '').strip()
            lines.append(('%s %s' % (title, link['url'])).strip())
            linked.add(str(link.get('host') or ''))
    lines += [str(host) for host in settings.get('websites') or [] if isinstance(host, str) and host not in linked]
    return '\n'.join(lines)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def editor_settings(fp_settings):
    """FairPlay's settings as form values; FairPlay's defaults for missing or unknown values."""
    fp_settings = fp_settings if isinstance(fp_settings, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    if fp_settings.get('calculator') in dict(FAIRPLAY_CALCULATOR_CHOICES):
        out['calculator'] = fp_settings['calculator']
    if fp_settings.get('review') in dict(FAIRPLAY_REVIEW_CHOICES):
        out['review'] = fp_settings['review']
    for field in ('open_book', 'strong_lock', 'resume_needs_approval'):
        if fp_settings.get(field) is not None:
            out[field] = _bool(fp_settings[field])
    days = _int(fp_settings.get('upload_opens_days'), DEFAULT_SETTINGS['upload_opens_days'])
    out['upload_opens_days'] = days if 0 <= days <= MAX_UPLOAD_DAYS else DEFAULT_SETTINGS['upload_opens_days']
    out['websites'] = websites_text(fp_settings)
    out['websites_blocked'] = [
        {'site': str(item.get('site') or ''), 'reason': str(item.get('reason') or '')}
        for item in fp_settings.get('websites_blocked') or [] if isinstance(item, dict) and item.get('site')
    ][:100]
    return out


def push_settings(contest_key, settings, acting_user=None):
    """Save settings in FairPlay with the short timeout; raises ``FairPlayError``."""
    from judge.utils.fairplay_client import FairPlayClient, QUICK_READ_TIMEOUT
    return FairPlayClient(acting_user=acting_user).set_settings(contest_key, settings,
                                                                read_timeout=QUICK_READ_TIMEOUT)


def stash_unsaved_settings(request, contest_key, settings, reason):
    """Keep settings FairPlay didn't take, to offer them again on the FairPlay tab."""
    try:
        unsaved = dict(request.session.get(UNSAVED_SESSION_KEY) or {})
        unsaved[contest_key] = {'settings': settings, 'reason': str(reason)[:300]}
        request.session[UNSAVED_SESSION_KEY] = dict(list(unsaved.items())[-10:])
    except Exception:
        logger.warning('Could not keep unsaved FairPlay settings of %s in the session', contest_key, exc_info=True)


def pop_unsaved_settings(request, contest_key, keep=False):
    try:
        unsaved = dict(request.session.get(UNSAVED_SESSION_KEY) or {})
    except Exception:
        logger.warning('Could not read unsaved FairPlay settings from the session', exc_info=True)
        return None
    item = unsaved.get(contest_key) if keep else unsaved.pop(contest_key, None)
    if not keep and item is not None:
        request.session[UNSAVED_SESSION_KEY] = unsaved
    return item if isinstance(item, dict) else None


class FairPlayEditorPlan:
    """The lock on or off (None: unchanged) and the settings to push after the save."""

    def __init__(self, want=None, settings=None, was_on=False):
        self.want, self.settings, self.was_on = want, settings, was_on


def parse_fairplay_payload(contest, payload, restricted):
    """Read before anything is saved; with the bridge off the lock can still be switched off."""
    was_on = is_fairplay_exam(contest)
    plan = FairPlayEditorPlan(was_on=was_on)
    if 'fairplay_required' in restricted or not (fairplay_enabled() or was_on):
        return plan
    if 'fairplay_required' in payload:
        plan.want = bool(payload.get('fairplay_required'))
        if plan.want and not was_on and not fairplay_enabled():
            plan.want = None
    if payload.get('fairplay_settings') is not None and fairplay_enabled():
        plan.settings = clean_settings(payload['fairplay_settings'])
    return plan


def apply_fairplay_editor(contest, plan):
    """Runs after the many-to-many save, since the tag picker leaves the FairPlay tag out."""
    on = plan.was_on if plan.want is None else plan.want
    set_fairplay_exam(contest, on)
    fairplay_check_contest(contest)


def _lead_hours():
    return int(FAIRPLAY_LEAD.total_seconds() // 3600)


def fairplay_edit_lock(contest):
    """Taken before an edit; None when nothing is locked."""
    return FairPlayEditLock.take(contest)


def fairplay_check_edit(lock, contest, plan):
    if lock is not None:
        lock.check(contest, plan)


class FairPlayEditLock:
    """Refuses a change of the exam's key, lock or problems while ``fairplay_edit_locked``."""

    def __init__(self, contest):
        self.key = contest.key
        self.problem_ids = set(contest.contest_problems.values_list('problem_id', flat=True))
        self.until = contest.end_time

    @classmethod
    def take(cls, contest, now=None):
        if contest is None or contest.pk is None or not fairplay_edit_locked(contest, now):
            return None
        return cls(contest)

    def check(self, contest, plan):
        if self.until is not None:
            until = _('until %(time)s (the end time + %(days)d days, while hand-ins can still arrive)') % {
                'time': date_format(timezone.localtime(self.until + DELIVERY_WINDOW), 'DATETIME_FORMAT'),
                'days': DELIVERY_WINDOW.days}
        else:
            until = _('until its results are finalized')
        reason = _('FairPlay holds the exam under its ID with these questions from %(hours)d hours before the '
                   'start %(until)s.') % {'hours': _lead_hours(), 'until': until}
        if contest.key != self.key:
            raise ValidationError(_("The assignment ID of a FairPlay exam can't be changed now. %s") % reason)
        if plan is not None and plan.want is False:
            raise ValidationError(_("The lock of a FairPlay exam can't be switched away from FairPlay now. %s")
                                  % reason)
        if set(contest.contest_problems.values_list('problem_id', flat=True)) != self.problem_ids:
            raise ValidationError(_("Problems can't be added to, removed from or replaced in a FairPlay exam now. "
                                    '%s') % reason)


def fairplay_shared_warning(contest, problems=None):
    if not is_fairplay_exam(contest):
        return None
    shared = list(fairplay_shared_problems(contest, problems).values_list('code', flat=True)[:21])
    if not shared:
        return None
    names = ', '.join(shared[:20]) + (', ...' if len(shared) > 20 else '')
    start = date_format(timezone.localtime(fairplay_window_start(contest)), 'DATETIME_FORMAT')
    return _('These problems are also used elsewhere and will be hidden everywhere on DKU OJ from %(start)s '
             'until results are released: %(problems)s') % {'start': start, 'problems': names}


def fairplay_editor_context(request, contest, restricted):
    from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
    on = is_fairplay_exam(contest) if contest is not None else False
    context = {
        'fairplay_offered': fairplay_enabled() or on,
        'fairplay_calculator_choices': FAIRPLAY_CALCULATOR_CHOICES,
        'fairplay_review_choices': FAIRPLAY_REVIEW_CHOICES,
        'fairplay_review_help': FAIRPLAY_REVIEW_HELP,
        'fairplay_on': on,
        'fairplay_settings': dict(DEFAULT_SETTINGS, websites='', websites_blocked=[]),
        'fairplay_settings_error': None,
        'fairplay_shared_warning': None,
        'fairplay_edit_locked': False,
        'fairplay_lead_hours': _lead_hours(),
        'fairplay_delivery_days': DELIVERY_WINDOW.days,
    }
    if contest is not None and on:
        context['fairplay_shared_warning'] = fairplay_shared_warning(contest)
        context['fairplay_edit_locked'] = fairplay_edit_locked(contest)
    if 'fairplay_required' in restricted or not fairplay_enabled() or contest is None or not on:
        return context
    try:
        view = FairPlayClient(acting_user=request.user.username).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
    except FairPlayError as e:
        # None tells the template FairPlay could not be reached.
        context['fairplay_settings'] = None
        context['fairplay_settings_error'] = e.detail
        return context
    context['fairplay_settings'] = editor_settings(view.get('settings'))
    return context


class FairPlaySaveResult:

    def __init__(self, warnings=(), not_saved=None, settings=None):
        self.warnings, self.not_saved, self.settings = list(warnings), not_saved, settings


def fairplay_save_settings(acting_user, contest, plan, old_key=None):
    """After the editor's save: send changed settings (or, on a key change, the old ones) to FairPlay."""
    from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
    if not fairplay_enabled() or not is_fairplay_exam(contest):
        return FairPlaySaveResult()
    settings = plan.settings
    if settings is None and old_key and old_key != contest.key:
        try:
            view = FairPlayClient(acting_user=acting_user).exam(old_key, read_timeout=QUICK_READ_TIMEOUT)
        except FairPlayError as e:
            return FairPlaySaveResult(not_saved=e.detail)
        try:
            settings = clean_settings(editor_settings(view.get('settings')))
        except ValidationError as e:
            logger.warning('FairPlay settings of %s could not be carried to %s: %s', old_key, contest.key,
                           ' '.join(e.messages))
            return FairPlaySaveResult(not_saved=' '.join(e.messages))
    if settings is None:
        return FairPlaySaveResult()
    try:
        view = push_settings(contest.key, settings, acting_user=acting_user)
    except FairPlayError as e:
        return FairPlaySaveResult(not_saved=e.detail, settings=settings)
    blocked = editor_settings((view or {}).get('settings') if isinstance(view, dict) else None)['websites_blocked']
    if not blocked:
        return FairPlaySaveResult()
    return FairPlaySaveResult(warnings=[_('FairPlay will not open these websites: %s.') % ', '.join(
        '%s (%s)' % (item['site'], item['reason']) if item['reason'] else item['site'] for item in blocked[:10])])


def fairplay_contest_context(request, contest):
    if not is_fairplay_exam(contest) or not contest.is_editable_by(request.user):
        return {'fairplay_box': None}
    from judge.utils.fairplay_delivery import hand_ins, delivery_warnings
    enabled = fairplay_enabled()
    rows = []
    for hand_in in hand_ins(contest):
        rows.append(dict(hand_in, student_url=(
            reverse('contest_fairplay_report', args=[contest.key, hand_in['profile'].user.username])
            if enabled else None)))
    return {'fairplay_box': {
        'tab_url': reverse('contest_fairplay', args=[contest.key]) if enabled else None,
        'receipts': rows,
        'warnings': delivery_warnings(contest),
        'lead_hours': _lead_hours(),
        'delivery_days': DELIVERY_WINDOW.days,
    }}
