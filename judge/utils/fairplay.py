"""FairPlay exams: the ``fairplay`` contest tag, the hiding rule and the assignment checks."""
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _, gettext_lazy as _lazy

__all__ = [
    'DELIVERY_WINDOW', 'FAIRPLAY_CALCULATOR_CHOICES', 'FAIRPLAY_LEAD', 'FAIRPLAY_MIN_TOKEN_LENGTH',
    'FAIRPLAY_REVIEW_CHOICES', 'FAIRPLAY_REVIEW_HELP', 'FAIRPLAY_TAG', 'KEY_ADDED_MINUTES', 'KEY_FAIRPLAY_CLOSES',
    'MAX_ADDED_MINUTES', 'fairplay_audience_error', 'fairplay_check_contest', 'fairplay_clean_admin_form',
    'fairplay_edit_locked', 'fairplay_enabled', 'fairplay_exams', 'fairplay_hidden_for',
    'fairplay_hidden_problem_ids', 'fairplay_hidden_q', 'fairplay_hiding_active', 'fairplay_in_window',
    'fairplay_public_list', 'fairplay_public_problems', 'fairplay_released', 'fairplay_shared_problems',
    'fairplay_tag', 'fairplay_tags_changed', 'fairplay_template_problem', 'fairplay_token', 'fairplay_tokens',
    'fairplay_url_insecure', 'fairplay_visible_breakdown', 'fairplay_visible_problems', 'fairplay_window_start',
    'is_fairplay_exam', 'set_fairplay_exam',
]

FAIRPLAY_MIN_TOKEN_LENGTH = 32
# FairPlay may download the package this long before the start.
FAIRPLAY_LEAD = timedelta(hours=24)
# FairPlay retries a hand-in for 14 days; DKU OJ accepts hand-ins until the exam's close + this.
DELIVERY_WINDOW = timedelta(days=14)
# Written by fairplay_delivery: minutes added on the FairPlay tab, latest close FairPlay reported.
KEY_ADDED_MINUTES = 'fpb:added-minutes:%d'
KEY_FAIRPLAY_CLOSES = 'fpb:fairplay-closes:%d'
# Without DKU OJ's count, an exam is taken to close at most this long after its end time.
MAX_ADDED_MINUTES = 8 * 60

# Set by the editor's FairPlay lock option (or in the admin), never offered in the tag picker.
FAIRPLAY_TAG = 'fairplay'
FAIRPLAY_TAG_COLOR = '#1f6feb'
FAIRPLAY_TAG_DESCRIPTION = 'Taken in the FairPlay app. Set by the FairPlay lock option of the assignment editor.'

# Stored in FairPlay, edited here.
FAIRPLAY_CALCULATOR_CHOICES = (
    ('off', _lazy('Off')),
    ('scientific', _lazy('Scientific')),
    ('graphing', _lazy('Graphing')),
)
FAIRPLAY_REVIEW_CHOICES = (
    ('nothing', _lazy('Nothing (just "Handed in")')),
    ('score', _lazy('Their grade only')),
    ('breakdown', _lazy('Their grade and points per question')),
    ('answers', _lazy('Their own answers (no scores)')),
    ('answers_and_results', _lazy('Everything: answers, scores and feedback')),
)
FAIRPLAY_REVIEW_HELP = {
    'nothing': _lazy('Students only see that their exam was handed in.'),
    'score': _lazy('The total score and percent once you release results. No questions or per-question points.'),
    'breakdown': _lazy('The total and the points on each question (number and title only). '
                       'No question text, answers or feedback.'),
    'answers': _lazy('Their own answers, read-only. No scores, correct answers or feedback.'),
    'answers_and_results': _lazy('Their answers, plus the points per question, correct answers and feedback '
                                 'once you release results.'),
}


def fairplay_tokens():
    """One token, or two during a rotation: calls out are signed with the first, calls in accept either."""
    value = getattr(settings, 'FAIRPLAY_TOKEN', '') or ''
    if isinstance(value, str):
        value = value.split(',')
    tokens = []
    for token in value:
        token = str(token or '').strip()
        if len(token) >= FAIRPLAY_MIN_TOKEN_LENGTH and token not in tokens:
            tokens.append(token)
    return tokens[:2]


def fairplay_token():
    """The signing token, or '' when the bridge is off."""
    tokens = fairplay_tokens()
    return tokens[0] if tokens else ''


def fairplay_enabled():
    return bool(fairplay_token())


def fairplay_url_insecure(url=None):
    """Bridge responses are not signed, so plain http needs the loopback, DEBUG or FAIRPLAY_ALLOW_INSECURE."""
    from urllib.parse import urlsplit
    url = (getattr(settings, 'FAIRPLAY_URL', '') if url is None else url) or ''
    if not url.lower().startswith('http://'):
        return False
    if settings.DEBUG or getattr(settings, 'FAIRPLAY_ALLOW_INSECURE', False) is True:
        return False
    try:
        host = (urlsplit(url).hostname or '').lower()
    except ValueError:
        return True
    if host == 'localhost' or host.endswith('.localhost'):
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def fairplay_exams(queryset=None):
    from judge.models import Contest
    queryset = Contest.objects.all() if queryset is None else queryset
    return queryset.filter(id__in=Contest.objects.filter(tags__name=FAIRPLAY_TAG).values('id'))


def is_fairplay_exam(contest):
    """Uses prefetched tags when present, else one query cached on the instance."""
    if contest is None or contest.pk is None:
        return False
    prefetched = getattr(contest, '_prefetched_objects_cache', {}).get('tags')
    if prefetched is not None:
        return any(tag.name == FAIRPLAY_TAG for tag in prefetched)
    cached = getattr(contest, '_fairplay_tagged', None)
    if cached is None:
        cached = contest.tags.filter(name=FAIRPLAY_TAG).exists()
        contest._fairplay_tagged = cached
    return cached


def fairplay_tag(create=False):
    from judge.models import ContestTag
    if create:
        return ContestTag.objects.get_or_create(name=FAIRPLAY_TAG, defaults={
            'color': FAIRPLAY_TAG_COLOR, 'description': FAIRPLAY_TAG_DESCRIPTION})[0]
    return ContestTag.objects.filter(name=FAIRPLAY_TAG).first()


def set_fairplay_exam(contest, on):
    tag = fairplay_tag(create=bool(on))
    if on:
        contest.tags.add(tag)
    elif tag is not None:
        contest.tags.remove(tag)
    getattr(contest, '_prefetched_objects_cache', {}).pop('tags', None)
    contest._fairplay_tagged = bool(on)


_KEY_ANY_EXAM = 'fpb:any-exam'
_ANY_EXAM_TTL = 3600


def fairplay_hiding_active():
    """Whether any contest has the FairPlay tag (cached; lets the rule skip its queries)."""
    flag = cache.get(_KEY_ANY_EXAM)
    if flag is None:
        from judge.models import Contest
        flag = int(Contest.objects.filter(tags__name=FAIRPLAY_TAG).exists())
        cache.set(_KEY_ANY_EXAM, flag, _ANY_EXAM_TTL)
    return bool(flag)


def fairplay_tags_changed(added):
    """Refresh the ``fairplay_hiding_active`` flag after a tag change."""
    if added:
        cache.set(_KEY_ANY_EXAM, 1, _ANY_EXAM_TTL)
        transaction.on_commit(lambda: cache.set(_KEY_ANY_EXAM, 1, _ANY_EXAM_TTL))
    else:
        cache.delete(_KEY_ANY_EXAM)
        transaction.on_commit(lambda: cache.delete(_KEY_ANY_EXAM))


def fairplay_window_start(contest):
    return contest.start_time - FAIRPLAY_LEAD


def _closes_keys(contest_id):
    return [KEY_ADDED_MINUTES % contest_id, KEY_FAIRPLAY_CLOSES % contest_id]


def _added_after_end(contest_id, end_time, values):
    """Added minutes, else FairPlay's reported close, else the cap."""
    added = values.get(KEY_ADDED_MINUTES % contest_id)
    if added is not None:
        try:
            return timedelta(minutes=max(0, int(added)))
        except (TypeError, ValueError):
            pass
    closes = values.get(KEY_FAIRPLAY_CLOSES % contest_id)
    if isinstance(closes, str):
        parsed = parse_datetime(closes.replace('Z', '+00:00'))
        if parsed is not None and timezone.is_aware(parsed):
            minutes = int((parsed - end_time).total_seconds() // 60)
            return timedelta(minutes=max(0, min(minutes, MAX_ADDED_MINUTES)))
    return timedelta(minutes=MAX_ADDED_MINUTES)


def fairplay_released(contest, now=None):
    """Finalized and closed for everyone, or past the delivery window."""
    now = now or timezone.now()
    if contest.end_time is None:
        return False
    if now >= contest.end_time + DELIVERY_WINDOW:
        return True
    if not contest.results_finalized or now < contest.end_time:
        return False
    values = cache.get_many(_closes_keys(contest.id))
    return now >= contest.end_time + _added_after_end(contest.id, contest.end_time, values)


def fairplay_in_window(contest, now=None):
    now = now or timezone.now()
    return now >= fairplay_window_start(contest) and not fairplay_released(contest, now)


def fairplay_edit_locked(contest, now=None):
    """Whether the editor must keep the exam's key, lock and problems."""
    if not is_fairplay_exam(contest):
        return False
    now = now or timezone.now()
    if now < fairplay_window_start(contest):
        return False
    if contest.end_time is None:
        return not contest.results_finalized
    return now < contest.end_time + DELIVERY_WINDOW


def _window_q(now):
    """FairPlay exams hiding themselves now."""
    from judge.models import Contest
    rows = list(Contest.objects.filter(tags__name=FAIRPLAY_TAG, start_time__lte=now + FAIRPLAY_LEAD)
                .filter(Q(end_time__isnull=True) | Q(end_time__gt=now - DELIVERY_WINDOW))
                .values_list('id', 'end_time', 'results_finalized').distinct())
    finalized = [(cid, end) for cid, end, done in rows if done and end is not None and end <= now]
    released = set()
    if finalized:
        values = cache.get_many([key for cid, _end in finalized for key in _closes_keys(cid)])
        released = {cid for cid, end in finalized if now >= end + _added_after_end(cid, end, values)}
    return Q(id__in=sorted({cid for cid, _end, _done in rows} - released))


def _sees_all_contests(user):
    return user.is_authenticated and (
        user.has_perm('judge.see_private_contest') or user.has_perm('judge.edit_all_contest'))


def _sees_all_problems(user):
    return user.is_authenticated and (
        user.has_perm('judge.see_private_problem') or
        (user.has_perm('judge.edit_own_problem') and user.has_perm('judge.edit_all_problem')))


def _contest_staff_q(profile):
    from judge.models import Organization
    teaching_orgs = Organization.objects.filter(
        Q(instructors=profile) | Q(admins=profile) | Q(class__admins=profile)).values('id')
    return (Q(authors=profile) | Q(curators=profile) | Q(testers=profile) |
            Q(organizations__in=teaching_orgs) | Q(classes__organization__in=teaching_orgs))


def _problem_staff_q(profile):
    return Q(authors=profile) | Q(curators=profile) | Q(testers=profile)


def _hidden_contests(user, now):
    from judge.models import Contest
    window = _window_q(now)
    queryset = Contest.objects.filter(window)
    if user.is_authenticated:
        staff = Contest.objects.filter(window).filter(_contest_staff_q(user.profile)).values('id')
        queryset = queryset.exclude(id__in=staff)
    from judge.utils.fairplay_native_scope import admitted_contest_id
    admitted_id = admitted_contest_id(user)
    if admitted_id is not None:
        queryset = queryset.exclude(pk=admitted_id)
    return queryset


def _hidden_contest_ids(user, now=None):
    if _sees_all_contests(user) or not fairplay_hiding_active():
        return []
    return sorted(set(_hidden_contests(user, now or timezone.now()).values_list('id', flat=True)))


def _hidden_problem_ids(user, now=None, contest_ids=None):
    from judge.models import ContestProblem, Problem
    if _sees_all_problems(user):
        return []
    if contest_ids is None:
        contest_ids = _hidden_contest_ids(user, now)
    if not contest_ids:
        return []
    queryset = Problem.objects.filter(id__in=ContestProblem.objects.filter(contest_id__in=contest_ids)
                                      .values('problem_id'))
    if user.is_authenticated:
        queryset = queryset.exclude(id__in=Problem.objects.filter(_problem_staff_q(user.profile)).values('id'))
    from judge.utils.fairplay_native_scope import admitted_contest_id
    admitted_id = admitted_contest_id(user)
    if admitted_id is not None:
        queryset = queryset.exclude(id__in=ContestProblem.objects.filter(contest_id=admitted_id).values('problem_id'))
    return sorted(set(queryset.values_list('id', flat=True)))


def _problem_hidden(user, problem, now):
    from judge.models import Problem, ContestProblem
    from judge.utils.fairplay_native_scope import admitted_contest_id
    admitted_id = admitted_contest_id(user)
    if admitted_id is not None and ContestProblem.objects.filter(contest_id=admitted_id, problem_id=problem.pk).exists():
        return False
    if _sees_all_problems(user) or _sees_all_contests(user) or not fairplay_hiding_active():
        return False
    if not _hidden_contests(user, now).filter(contest_problems__problem_id=problem.id).exists():
        return False
    return not (user.is_authenticated and
                Problem.objects.filter(_problem_staff_q(user.profile), id=problem.id).exists())


def _attachment_hidden(user, attachment, now):
    """Referenced from a hidden exam's description or from any served text of one of its problems."""
    from judge.models import Contest, Problem
    needle = str(attachment.uuid)
    contest_ids = _hidden_contest_ids(user, now)
    if not contest_ids:
        return False
    if Contest.objects.filter(id__in=contest_ids, description__contains=needle).exists():
        return True
    problem_ids = _hidden_problem_ids(user, now, contest_ids)
    if not problem_ids:
        return False
    fields = ('description', 'summary', 'translations__description', 'mcq_options__text',
              'file_config__instructions', 'solution__content', 'problemclarification__description')
    q = Q()
    for field in fields:
        q |= Q(**{field + '__contains': needle})
    return Problem.objects.filter(q, id__in=problem_ids).exists()


def fairplay_hidden_for(user, obj):
    """Whether ``user`` must not see ``obj`` (a contest, problem, contest problem, submission or attachment)."""
    from judge.models import Contest, ContestProblem, EditorAttachment, Problem, Submission
    if isinstance(obj, type):
        raise TypeError('fairplay_hidden_for takes an instance; use fairplay_hidden_q for a model')
    now = timezone.now()
    if isinstance(obj, Contest):
        from judge.utils.fairplay_native_scope import admitted_contest_id
        if admitted_contest_id(user) == obj.pk:
            return False
        if not is_fairplay_exam(obj) or not fairplay_in_window(obj, now):
            return False
        return not _is_contest_staff(user, obj)
    if isinstance(obj, Problem):
        return _problem_hidden(user, obj, now)
    if isinstance(obj, ContestProblem):
        return fairplay_hidden_for(user, obj.contest) or _problem_hidden(user, obj.problem, now)
    if isinstance(obj, Submission):
        if obj.contest_object_id is not None and fairplay_hidden_for(user, obj.contest_object):
            return True
        return _problem_hidden(user, obj.problem, now)
    if isinstance(obj, EditorAttachment):
        return _attachment_hidden(user, obj, now)
    raise TypeError('fairplay_hidden_for: unsupported object %r' % type(obj))


def _is_contest_staff(user, contest):
    if not user.is_authenticated:
        return False
    if _sees_all_contests(user):
        return True
    profile_id = user.profile.id
    return (profile_id in contest.editor_ids or profile_id in contest.tester_ids or
            contest.is_viewable_by_org_admin(user) or contest.is_viewable_by_course_teacher(user))


def fairplay_hidden_q(user, model):
    """A ``Q`` of the rows of ``model`` hidden from ``user``, for ``exclude``."""
    from judge.models import Contest, Problem, Submission
    now = timezone.now()
    if model is Contest:
        return Q(pk__in=_hidden_contest_ids(user, now))
    if model is Problem:
        return Q(pk__in=_hidden_problem_ids(user, now))
    if model is Submission:
        contest_ids = _hidden_contest_ids(user, now)
        if not contest_ids:
            return Q(pk__in=[])
        q = Q(contest_object_id__in=contest_ids)
        problem_ids = _hidden_problem_ids(user, now, contest_ids)
        if problem_ids:
            q |= Q(problem_id__in=problem_ids)
        return q
    raise TypeError('fairplay_hidden_q: unsupported model %r' % model)


def fairplay_template_problem(user, problem_id):
    if not problem_id:
        return problem_id
    from judge.models import Problem
    problem = Problem.objects.filter(id=problem_id).first() if str(problem_id).isdigit() else None
    if problem is None or fairplay_hidden_for(user, problem):
        return None
    return problem_id


def fairplay_hidden_problem_ids(user):
    return _hidden_problem_ids(user)


def fairplay_visible_problems(user, queryset, field='problem_id'):
    """``queryset`` without rows whose problem (``field``) is hidden from ``user``."""
    problem_ids = _hidden_problem_ids(user)
    if not problem_ids:
        return queryset
    return queryset.exclude(**{field + '__in': problem_ids})


def fairplay_visible_breakdown(user, entries):
    problem_ids = _hidden_problem_ids(user)
    if not problem_ids:
        return entries
    from judge.models import Problem
    codes = set(Problem.objects.filter(id__in=problem_ids).values_list('code', flat=True))
    return [entry for entry in entries if entry.problem_code not in codes]


def fairplay_public_problems(queryset=None):
    from django.contrib.auth.models import AnonymousUser
    from judge.models import Problem
    queryset = Problem.get_public_problems() if queryset is None else queryset
    return fairplay_visible_problems(AnonymousUser(), queryset, field='id')


def fairplay_public_list(problems):
    """Public problems for display (not for points)."""
    from django.contrib.auth.models import AnonymousUser
    hidden = set(_hidden_problem_ids(AnonymousUser()))
    return [problem for problem in problems if problem.id not in hidden] if hidden else problems


def fairplay_shared_problems(contest, problems=None):
    from judge.models import ContestProblem, Problem
    if problems is None:
        problems = Problem.objects.filter(contests__contest=contest)
    others = ContestProblem.objects.exclude(contest_id=contest.pk).values('problem_id')
    return (problems.filter(Q(is_public=True) | Q(id__in=others)).distinct().order_by('code'))


def fairplay_audience_error(contest, seb_required=None, is_private=None, is_organization_private=None,
                            has_courses=None, has_private_contestants=None):
    """Why ``contest`` can't be a FairPlay exam, or None; unsaved form values may be passed."""
    def given(value, read):
        return read() if value is None else value

    seb_required = given(seb_required, lambda: contest.seb_required)
    is_private = given(is_private, lambda: contest.is_private)
    is_organization_private = given(is_organization_private, lambda: contest.is_organization_private)
    if seb_required:
        return _('Choose either Safe Exam Browser or FairPlay as the lock, not both.')
    if not (is_private or is_organization_private):
        return _('A FairPlay assignment needs a student list: limit it to a course, classes or selected students.')
    if is_organization_private and not given(
            has_courses, lambda: contest.organizations.exists() or contest.classes.exists()):
        return _('A FairPlay assignment needs a student list: pick at least one course or class.')
    if is_private and not given(has_private_contestants, lambda: contest.private_contestants.exists()):
        return _('A FairPlay assignment needs a student list: add the students under Private assignees.')
    return None


def fairplay_clean_admin_form(form, cleaned_data):
    """Checks the values about to be saved, since the admin saves the tags after the form."""
    tags = cleaned_data.get('tags')
    if tags is None or not any(tag.name == FAIRPLAY_TAG for tag in tags):
        return
    instance = form.instance

    def value(name):
        return bool(cleaned_data[name] if name in cleaned_data else getattr(instance, name, None))

    def chosen(name):
        if name in cleaned_data:
            return bool(cleaned_data[name])
        return bool(instance.pk and getattr(instance, name).exists())

    error = fairplay_audience_error(
        instance, seb_required=value('seb_required'), is_private=value('is_private'),
        is_organization_private=value('is_organization_private'),
        has_courses=chosen('organizations') or chosen('classes'),
        has_private_contestants=chosen('private_contestants'))
    if error:
        raise ValidationError(error)


def fairplay_check_contest(contest):
    if not is_fairplay_exam(contest):
        return
    error = fairplay_audience_error(contest)
    if error:
        raise ValidationError(error)
