"""FairPlay in the assignment editor and on the assignment page; the exam settings are stored in FairPlay."""
import logging

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from judge.utils.fairplay import FAIRPLAY_CALCULATOR_CHOICES, FAIRPLAY_REVIEW_CHOICES, fairplay_check_contest, \
    fairplay_edit_locked, fairplay_enabled, is_fairplay_exam, set_fairplay_exam

__all__ = [
    'DEFAULT_SETTINGS', 'FAIRPLAY_EDITOR_FIELDS', 'FairPlayEditLock', 'FairPlayEditorPlan', 'FairPlaySaveResult',
    'apply_fairplay_editor', 'clean_settings', 'editor_settings', 'fairplay_check_edit',
    'fairplay_edit_lock', 'fairplay_editor_context', 'fairplay_save_settings',
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

    @classmethod
    def take(cls, contest, now=None):
        if contest is None or contest.pk is None or not fairplay_edit_locked(contest, now):
            return None
        return cls(contest)

    def check(self, contest, plan):
        if contest.key != self.key:
            raise ValidationError(_("The assignment ID can't be changed now."))
        if plan is not None and plan.want is False:
            raise ValidationError(_("The lock can't be changed now."), code='fairplay_lock')
        if set(contest.contest_problems.values_list('problem_id', flat=True)) != self.problem_ids:
            raise ValidationError(_("The problems can't be changed now."))


def fairplay_editor_context(request, contest, restricted):
    on = is_fairplay_exam(contest) if contest is not None else False
    return {'fairplay_offered': fairplay_enabled() or on, 'fairplay_on': on}


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
