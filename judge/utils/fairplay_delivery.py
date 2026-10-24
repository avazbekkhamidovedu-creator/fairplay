"""Answers FairPlay delivers after hand-in, and the released results it reads back."""
import hashlib
import logging
import secrets
import time
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from judge.models import ContestParticipation, Language, ProblemSubmissionType, Profile, Submission, SubmissionFile
from judge.storage import StorageUnavailableError
from judge.utils.fairplay import DELIVERY_WINDOW, KEY_ADDED_MINUTES, KEY_FAIRPLAY_CLOSES, MAX_ADDED_MINUTES
from judge.utils.fairplay_package import QUESTION_TYPES, canonical_json, code_languages, exam_roster, iso, \
    problem_question_id
from judge.utils.submission_create import create_submission

__all__ = ['DELIVERY_WINDOW', 'DeliveryError', 'active_hand_in', 'added_minutes', 'clear_hand_in',
           'deliver_submission', 'delivery_warnings', 'exam_closes_at', 'hand_ins', 'parse_time',
           'record_added_minutes', 'report_url_allowed', 'results_page', 'store_upload']

logger = logging.getLogger('judge.fairplay')

LATE_GRACE = timedelta(seconds=120)
# Longer than the delivery window, so an upload made before an outage outlives the retries.
UPLOAD_TTL = DELIVERY_WINDOW + timedelta(days=1)
RECORD_TTL = int(timedelta(days=60).total_seconds())
MAX_ADDED_TTL = int(timedelta(days=180).total_seconds())
LOCK_SECONDS = 60
LOCK_WAIT_SECONDS = 20
RETRY_AFTER_SECONDS = 30
# Caps FairPlay's close time when DKU OJ's own count of added minutes is lost.
MAX_ADDED_MINUTES_FROM_FAIRPLAY = MAX_ADDED_MINUTES
MAX_WARNINGS = 50
MAX_SOURCE_LENGTH = 65536
MAX_TEXT_LENGTH = 100000
PAGE_SIZE = 100
UPLOAD_PREFIX = 'fairplay_uploads/'

_KEY_ATTEMPT = 'fpb:attempt:%s'
_KEY_HAND_IN = 'fpb:handin:%d:%d'
_KEY_CLEARED = 'fpb:cleared:%d:%d'
_KEY_LOCK = 'fpb:lock:%d:%d'
_KEY_WARNINGS = 'fpb:warnings:%d'
_KEY_ADDED = KEY_ADDED_MINUTES
_KEY_UPLOAD = 'fpb:upload:%s'
_KEY_RESULTS = 'fpb:results:%d'


class DeliveryError(Exception):
    """Answered to FairPlay as ``status``; ``retry_after`` marks a failure FairPlay should retry."""

    def __init__(self, status, detail, question_id=None, retry_after=None):
        super().__init__(detail)
        self.status = status
        self.retry_after = retry_after
        self.body = {'detail': detail}
        if question_id is not None:
            self.body = {'question_id': question_id, 'detail': detail}


def transient_error(detail='DKU OJ could not store the hand-in right now; try again.'):
    return DeliveryError(503, detail, retry_after=RETRY_AFTER_SECONDS)


TRANSIENT_ERRORS = (DatabaseError, OSError, StorageUnavailableError)


def _closed_too_long_ago():
    return DeliveryError(410, 'This exam closed more than %d days ago; DKU OJ no longer accepts hand-ins for it.'
                         % DELIVERY_WINDOW.days)


def parse_time(value, field):
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        raise DeliveryError(422, '%s must be an ISO-8601 time.' % field)
    parsed = parse_datetime(value.replace('Z', '+00:00'))
    if parsed is None:
        raise DeliveryError(422, '%s must be an ISO-8601 time.' % field)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _profile_on_roster(contest, username):
    if not isinstance(username, str) or not username:
        return None
    profile = Profile.objects.filter(user__username=username).select_related('user').first()
    if profile is None or not exam_roster(contest).filter(id=profile.id).exists():
        return None
    return profile


def _contest_problems_by_question(contest):
    return {problem_question_id(cp.problem_id): cp
            for cp in contest.contest_problems.select_related('problem')}


def _added_ttl(contest):
    left = (contest.end_time - timezone.now()).total_seconds() if contest.end_time else 0
    return int(min(MAX_ADDED_TTL, max(RECORD_TTL, left + RECORD_TTL)))


def record_added_minutes(contest, minutes):
    """Count minutes a professor added on the FairPlay tab (FairPlay closes that much later)."""
    key, ttl = _KEY_ADDED % contest.id, _added_ttl(contest)
    if not cache.add(key, int(minutes), ttl):
        try:
            cache.incr(key, int(minutes))
        except ValueError:
            cache.set(key, int(minutes), ttl)
        cache.touch(key, ttl)


def note_fairplay_closes(contest, closes_at):
    """Keep the latest close time FairPlay reported (used when the added minutes are lost)."""
    if closes_at is None:
        return
    key = KEY_FAIRPLAY_CLOSES % contest.id
    stored = cache.get(key)
    known = parse_datetime(stored.replace('Z', '+00:00')) if isinstance(stored, str) else None
    if known is None or closes_at > known:
        cache.set(key, iso(closes_at), _added_ttl(contest))


def added_minutes(contest, fairplay_closes_at=None):
    value = cache.get(_KEY_ADDED % contest.id)
    if value is not None:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    if fairplay_closes_at is not None and contest.end_time is not None:
        minutes = int((fairplay_closes_at - contest.end_time).total_seconds() // 60)
        return max(0, min(minutes, MAX_ADDED_MINUTES_FROM_FAIRPLAY))
    return 0


def exam_closes_at(contest, fairplay_closes_at=None):
    """The end time plus the minutes added from DKU OJ; there is no per-student extra time."""
    return contest.end_time + timedelta(minutes=added_minutes(contest, fairplay_closes_at))


# The path names the contest, student and problem, so an upload id only works for its own answer.
def _storage():
    from judge.storage import submission_storage
    return submission_storage()


def _upload_prefix(contest_id, profile_id, problem_id, token):
    return '%s%d/%d/%d/%s' % (UPLOAD_PREFIX, contest_id, profile_id, problem_id, token)


def _sweep_uploads(now):
    from judge.models.stored_file import StoredFileRecord
    stale = (StoredFileRecord.objects.filter(path__startswith=UPLOAD_PREFIX, created_on__lt=now - UPLOAD_TTL)
             .exclude(status=StoredFileRecord.STATUS_DELETED).values_list('path', flat=True)[:100])
    storage = _storage()
    for path in list(stale):
        try:
            storage.delete(path)
        except Exception:
            logger.warning('Could not delete the unclaimed FairPlay upload %s', path, exc_info=True)


def store_upload(contest, username, question_id, filename, sha256, data):
    """Store one file answer ahead of its submission; return its upload id."""
    from judge.models import ProblemFileConfig
    from judge.storage import generate_submission_key
    now = timezone.now()
    _sweep_uploads(now)
    if now > exam_closes_at(contest) + DELIVERY_WINDOW:
        raise _closed_too_long_ago()
    profile = _profile_on_roster(contest, username)
    if profile is None:
        raise DeliveryError(403, 'Student is not on the roster of this exam.')
    contest_problem = _contest_problems_by_question(contest).get(question_id)
    if contest_problem is None or contest_problem.problem.submission_type != ProblemSubmissionType.FILE:
        raise DeliveryError(422, 'Not a file-upload question of this exam.', question_id)
    problem = contest_problem.problem
    filename = (filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip()[:255]
    if not filename:
        raise DeliveryError(422, 'filename is required.', question_id)
    try:
        config = problem.file_config
    except ProblemFileConfig.DoesNotExist:
        config = None
    max_size = config.max_file_size if config else ProblemFileConfig._meta.get_field('max_file_size').get_default()
    if len(data) > max_size:
        raise DeliveryError(422, 'File is larger than this question allows.', question_id)
    allowed = config.allowed_extension_list() if config else []
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if allowed and extension not in allowed:
        raise DeliveryError(422, 'File type .%s is not allowed for this question.' % extension, question_id)
    if not data:
        raise DeliveryError(422, 'The file is empty.', question_id)
    if not isinstance(sha256, str) or hashlib.sha256(data).hexdigest() != sha256.lower():
        raise DeliveryError(422, 'X-Sha256 does not match the file.', question_id)
    token = secrets.token_hex(12)
    # The extension as the store's key generator keeps it (whitelisted characters only).
    ext = generate_submission_key(0, filename).rsplit('/', 1)[-1][32:]
    try:
        _storage().save(_upload_prefix(contest.id, profile.id, problem.id, token) + ext, ContentFile(data))
    except TRANSIENT_ERRORS:
        logger.exception('Could not store a FairPlay upload for %s', contest.key)
        raise transient_error('DKU OJ could not store the file right now; try again.')
    cache.set(_KEY_UPLOAD % token, {'name': filename, 'size': len(data)}, int(UPLOAD_TTL.total_seconds()))
    return 'up_' + token


def _claim_upload(contest, profile, problem, upload_id):
    """An open handle for this student's upload to this question, or None; deleted after the commit."""
    from judge.models.stored_file import StoredFileRecord
    if not isinstance(upload_id, str) or not upload_id.startswith('up_') or len(upload_id) != 27:
        return None
    token = upload_id[3:]
    if any(c not in '0123456789abcdef' for c in token):
        return None
    path = (StoredFileRecord.objects.filter(path__startswith=_upload_prefix(contest.id, profile.id, problem.id, token))
            .exclude(status=StoredFileRecord.STATUS_DELETED).values_list('path', flat=True).first())
    storage = _storage()
    if path is None:
        return None
    try:
        size = storage.size(path)
        handle = File(storage.open(path, 'rb'))
    except FileNotFoundError:
        logger.warning('The FairPlay upload %s is recorded but missing', path)
        return None
    except TRANSIENT_ERRORS:
        logger.exception('Could not open the FairPlay upload %s', path)
        raise transient_error('DKU OJ could not read an uploaded file right now; try again.')
    meta = cache.get(_KEY_UPLOAD % token) or {}
    prefix = _upload_prefix(contest.id, profile.id, problem.id, token)
    handle.name = str(meta.get('name') or ('answer' + path[len(prefix):]))
    handle.size = size
    transaction.on_commit(lambda: _delete_upload(path, token), robust=True)
    return handle


def _delete_upload(path, token):
    try:
        _storage().delete(path)
    finally:
        cache.delete(_KEY_UPLOAD % token)


def _attempt_key(attempt_id):
    return _KEY_ATTEMPT % hashlib.sha256(attempt_id.encode('utf-8')).hexdigest()


def _filed_submissions(contest, profile):
    """Submissions newer than the professor's last clear."""
    queryset = Submission.objects.filter(contest_object=contest, user=profile,
                                         contest__participation__virtual=ContestParticipation.LIVE)
    cleared = cache.get(_KEY_CLEARED % (contest.id, profile.id))
    if isinstance(cleared, dict):
        queryset = queryset.filter(id__gt=int(cleared.get('max_submission') or 0))
    return queryset


def _record_out(record):
    """A hand-in record for pages (times parsed)."""
    if not isinstance(record, dict):
        return None
    out = dict(record)
    for field in ('received_at', 'handed_in_at', 'started_at', 'deadline_at'):
        try:
            out[field] = parse_time(record.get(field), field)
        except DeliveryError:
            out[field] = None
    out['report_url'] = record.get('report_url') or record.get('integrity_report_url') or ''
    out['warnings'] = [str(w) for w in record.get('warnings') or [] if isinstance(w, str)]
    out['record_missing'] = False
    return out


def _missing_record(first, count):
    return {'record_missing': True, 'received_at': first, 'handed_in_at': None, 'is_late': None,
            'report_url': '', 'attempt_id': '', 'submission_count': count, 'warnings': []}


def active_hand_in(contest, profile):
    """The hand-in that counts: its record, a ``record_missing`` stand-in when the cache lost it, or None."""
    record = cache.get(_KEY_HAND_IN % (contest.id, profile.id))
    if isinstance(record, dict):
        return _record_out(record)
    filed = _filed_submissions(contest, profile)
    first = filed.order_by('date').values_list('date', flat=True).first()
    if first is None:
        return None
    return _missing_record(first, filed.count())


def clear_hand_in(contest, profile):
    """Let FairPlay deliver another attempt; filed submissions stay. True when there was one to clear."""
    had = (cache.get(_KEY_HAND_IN % (contest.id, profile.id)) is not None or
           _filed_submissions(contest, profile).exists())
    if not had:
        return False
    newest = Submission.objects.filter(contest_object=contest, user=profile).aggregate(m=Max('id'))['m'] or 0
    cache.set(_KEY_CLEARED % (contest.id, profile.id), {'max_submission': newest, 'at': iso(timezone.now())},
              RECORD_TTL)
    cache.delete(_KEY_HAND_IN % (contest.id, profile.id))
    return True


def _filed_without_record(contest, profile_ids):
    """``{profile_id: (first date, count)}`` of filed submissions, one query for all."""
    if not profile_ids:
        return {}
    cleared = cache.get_many([_KEY_CLEARED % (contest.id, pid) for pid in profile_ids])
    q = Q()
    for pid in profile_ids:
        mark = cleared.get(_KEY_CLEARED % (contest.id, pid))
        after = int(mark.get('max_submission') or 0) if isinstance(mark, dict) else 0
        q |= Q(user_id=pid, id__gt=after)
    rows = (Submission.objects.filter(q, contest_object=contest,
                                      contest__participation__virtual=ContestParticipation.LIVE)
            .values('user_id').annotate(first=Min('date'), count=Count('id')))
    return {row['user_id']: (row['first'], row['count']) for row in rows}


def hand_ins(contest):
    staff = set(contest.editor_ids) | set(contest.tester_ids)
    participations = list(contest.users.filter(virtual=ContestParticipation.LIVE).exclude(user_id__in=staff)
                          .select_related('user__user').order_by('user__user__username'))
    records = cache.get_many([_KEY_HAND_IN % (contest.id, p.user_id) for p in participations])
    on_file = _filed_without_record(contest, [p.user_id for p in participations
                                              if not isinstance(records.get(_KEY_HAND_IN % (contest.id, p.user_id)),
                                                                dict)])
    rows = []
    for participation in participations:
        profile = participation.user
        record = records.get(_KEY_HAND_IN % (contest.id, profile.id))
        if isinstance(record, dict):
            hand_in = _record_out(record)
        elif profile.id in on_file:
            hand_in = _missing_record(*on_file[profile.id])
        else:
            continue
        rows.append({
            'profile': profile,
            'report_url': hand_in.get('report_url') or '',
            'is_late': hand_in.get('is_late'),
            'received_at': hand_in.get('received_at'),
            'handed_in_at': hand_in.get('handed_in_at'),
            'record_missing': hand_in.get('record_missing'),
            'warnings': hand_in.get('warnings') or [],
        })
    return rows


def _warn(contest, profile, attempt_id, detail):
    key = _KEY_WARNINGS % contest.id
    warnings = list(cache.get(key) or [])
    warnings.append({'username': profile.user.username, 'attempt_id': attempt_id, 'at': iso(timezone.now()),
                     'detail': detail})
    cache.set(key, warnings[-MAX_WARNINGS:], RECORD_TTL)


def delivery_warnings(contest):
    """Hand-ins DKU OJ refused because answers were already on file without a record."""
    out = []
    for item in cache.get(_KEY_WARNINGS % contest.id) or []:
        if isinstance(item, dict):
            try:
                at = parse_time(item.get('at'), 'at')
            except DeliveryError:
                at = None
            out.append(dict(item, at=at))
    return out


class _StudentLock:
    """One delivery per student and exam at a time, across workers; the row lock is the backstop."""

    def __init__(self, contest, profile):
        self.key = _KEY_LOCK % (contest.id, profile.id)
        self.token = secrets.token_hex(8)

    def __enter__(self):
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while not cache.add(self.key, self.token, LOCK_SECONDS):
            if time.monotonic() > deadline:
                raise DeliveryError(503, 'Another delivery for this student is in progress; try again.',
                                    retry_after=RETRY_AFTER_SECONDS)
            time.sleep(0.2)
        return self

    def renew(self):
        if cache.get(self.key) == self.token:
            cache.touch(self.key, LOCK_SECONDS)

    def __exit__(self, *exc):
        if cache.get(self.key) == self.token:
            cache.delete(self.key)
        return False


def _answer_for(contest, profile, contest_problem, question_id, answer, warnings):
    """Translate one FairPlay answer into ``(language, answer)``; None when there is nothing to file."""
    problem = contest_problem.problem
    kind = QUESTION_TYPES.get(problem.submission_type, 'code')
    value = answer.get(kind)
    if not isinstance(value, dict) or any(k in answer for k in QUESTION_TYPES.values() if k != kind):
        raise DeliveryError(422, 'Expected a "%s" answer for this question.' % kind, question_id)

    if kind == 'choice':
        selected = value.get('selected')
        if not isinstance(selected, list) or not all(isinstance(i, int) and not isinstance(i, bool)
                                                     for i in selected):
            raise DeliveryError(422, 'choice.selected must be a list of option ids.', question_id)
        valid = set(problem.mcq_options.values_list('id', flat=True))
        unknown = set(selected) - valid
        selected = set(selected) & valid
        if unknown:
            warnings.append('Question %s: option%s %s no longer exist%s; %s.' % (
                question_id, 's' if len(unknown) > 1 else '', ', '.join(str(i) for i in sorted(unknown)),
                '' if len(unknown) > 1 else 's',
                'filed without them' if selected else 'the answer was not filed'))
            if not selected:
                return None
        try:
            multiple = problem.mcq_config.multiple_select
        except ObjectDoesNotExist:
            multiple = False
        if not multiple and len(selected) > 1:
            raise DeliveryError(422, 'Only one option may be selected.', question_id)
        return Language.get_answer_language(), {'selected': sorted(selected)}

    if kind == 'fill_blank':
        answers = value.get('answers')
        if not isinstance(answers, list) or not all(isinstance(a, str) for a in answers):
            raise DeliveryError(422, 'fill_blank.answers must be a list of strings.', question_id)
        if len(answers) > problem.blanks.count() or sum(len(a) for a in answers) > MAX_TEXT_LENGTH:
            raise DeliveryError(422, 'Too many or too long blank answers.', question_id)
        return Language.get_answer_language(), {'answers': answers}

    if kind == 'short_answer':
        text = value.get('text')
        if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
            raise DeliveryError(422, 'short_answer.text must be a string of at most %d characters.'
                                % MAX_TEXT_LENGTH, question_id)
        return Language.get_answer_language(), {'answer': text}

    if kind == 'file_upload':
        upload_ids = value.get('upload_ids')
        if not isinstance(upload_ids, list) or not all(isinstance(u, str) for u in upload_ids):
            raise DeliveryError(422, 'file_upload.upload_ids must be a list of upload ids.', question_id)
        if not upload_ids:
            return None
        if len(upload_ids) > 1:
            raise DeliveryError(422, 'A file-upload question takes one file.', question_id)
        handle = _claim_upload(contest, profile, problem, upload_ids[0])
        if handle is None:
            raise DeliveryError(422, 'Unknown or already used upload id.', question_id)
        return Language.get_answer_language(), {'file': handle}

    # code
    language_key, source = value.get('language'), value.get('source')
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE_LENGTH:
        raise DeliveryError(422, 'code.source must be non-empty and at most %d characters.'
                            % MAX_SOURCE_LENGTH, question_id)
    language = code_languages(problem).filter(key=language_key).first() if isinstance(language_key, str) else None
    if language is None:
        raise DeliveryError(422, 'Language is not allowed for this question.', question_id)
    return language, source


def _replay(record, contest, body_hash):
    if record.get('contest_id') != contest.id:
        raise DeliveryError(409, 'This attempt was already delivered for another exam.')
    if record.get('body_sha256') != body_hash:
        raise DeliveryError(409, 'This attempt was already delivered with different answers.')
    return _receipt(record)


def _receipt(record):
    return {'receipt': record.get('receipt'), 'submissions': record.get('submissions') or [],
            'warnings': [w for w in record.get('warnings') or [] if isinstance(w, str)]}


def report_url_allowed(url):
    if not isinstance(url, str) or not url or len(url) > 500:
        return False
    for base in (getattr(settings, 'FAIRPLAY_URL', ''), getattr(settings, 'FAIRPLAY_PUBLIC_URL', '')):
        base = (base or '').rstrip('/')
        if base.startswith(('https://', 'http://')) and url.startswith(base + '/'):
            return True
    return False


def _other_attempt(record):
    return DeliveryError(409, 'This student already has a FairPlay hand-in for this exam (attempt %s). '
                              "A professor can clear it on the student's FairPlay report in DKU OJ; "
                              'then deliver again.' % (record.get('attempt_id') or '?'))


ON_FILE_WITHOUT_RECORD = ('This student already has answers on file for this exam in DKU OJ, but DKU OJ no '
                          'longer has the record of that hand-in. To avoid filing the answers twice they were '
                          'not filed. A professor can check the submissions and clear the hand-in on the '
                          "student's FairPlay report in DKU OJ; then deliver again.")


def _already_handed_in(contest, profile, attempt_id, body_hash):
    """The receipt to replay, a 409, or None when nothing of this student is on file."""
    record = cache.get(_attempt_key(attempt_id))
    if isinstance(record, dict):
        return _replay(record, contest, body_hash)
    current = cache.get(_KEY_HAND_IN % (contest.id, profile.id))
    if isinstance(current, dict):
        raise _other_attempt(current)
    if _filed_submissions(contest, profile).exists():
        _warn(contest, profile, attempt_id, 'answers already on file without a hand-in record; not filed again')
        raise DeliveryError(409, ON_FILE_WITHOUT_RECORD)
    return None


def _stored_files(submission):
    return [(f.file.storage, f.file.name) for f in SubmissionFile.objects.filter(submission=submission) if f.file]


def _delete_stored(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.warning('Could not delete %s after a rolled-back FairPlay hand-in', name, exc_info=True)


def deliver_submission(contest, payload, now=None):
    """File one student's final answers, all or nothing. Returns the receipt body."""
    if not isinstance(payload, dict):
        raise DeliveryError(422, 'Expected a JSON object.')
    attempt_id = payload.get('attempt_id')
    if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 128:
        raise DeliveryError(422, 'attempt_id is required (at most 128 characters).')
    body_hash = hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()

    record = cache.get(_attempt_key(attempt_id))
    if isinstance(record, dict):
        return _replay(record, contest, body_hash)

    now = now or timezone.now()
    try:
        fairplay_closes_at = parse_time(payload.get('exam_closes_at'), 'exam_closes_at')
    except DeliveryError:
        fairplay_closes_at = None
    closes_at = exam_closes_at(contest, fairplay_closes_at)
    if now > closes_at + DELIVERY_WINDOW:
        raise _closed_too_long_ago()
    note_fairplay_closes(contest, fairplay_closes_at)
    profile = _profile_on_roster(contest, payload.get('username'))
    if profile is None:
        raise DeliveryError(403, 'Student is not on the roster of this exam.')
    evidence = payload.get('handed_in_evidence') or ''
    if evidence not in ('server', 'sealed'):
        raise DeliveryError(422, 'handed_in_evidence must be "server" or "sealed".')
    started_at = parse_time(payload.get('started_at'), 'started_at')
    deadline_at = parse_time(payload.get('deadline_at'), 'deadline_at')
    handed_in_at = parse_time(payload.get('handed_in_at'), 'handed_in_at')
    report_url = payload.get('integrity_report_url') or ''
    if not report_url_allowed(report_url):
        report_url = ''
    answers = payload.get('answers')
    if not isinstance(answers, list) or not all(isinstance(a, dict) for a in answers):
        raise DeliveryError(422, 'answers must be a list of objects.')
    by_question = _contest_problems_by_question(contest)
    seen, warnings, to_file = set(), [], []
    for answer in answers:
        question_id = answer.get('question_id')
        if not isinstance(question_id, str) or not question_id:
            raise DeliveryError(422, 'question_id is required.')
        if question_id in seen:
            raise DeliveryError(422, 'Question answered twice.', question_id)
        seen.add(question_id)
        if question_id not in by_question:
            warnings.append('Question %s is no longer in this exam; its answer was not filed.' % question_id[:40])
            continue
        to_file.append(answer)

    with _StudentLock(contest, profile) as lock:
        # Checked again under the lock: a parallel delivery of the same attempt has finished.
        replay = _already_handed_in(contest, profile, attempt_id, body_hash)
        if replay is not None:
            return replay
        stored, written = [], False
        keys = [_attempt_key(attempt_id), _KEY_HAND_IN % (contest.id, profile.id)]
        try:
            with transaction.atomic():
                # The cache lock may have expired: check again under the profile row lock.
                Profile.objects.select_for_update().filter(pk=profile.pk).first()
                replay = _already_handed_in(contest, profile, attempt_id, body_hash)
                if replay is not None:
                    return replay
                participation = ContestParticipation.objects.filter(
                    contest=contest, user=profile, virtual=ContestParticipation.LIVE).first()
                if participation is None:
                    participation = ContestParticipation.objects.create(
                        contest=contest, user=profile, virtual=ContestParticipation.LIVE,
                        # FairPlay's start, kept within the exam's own window and never in the future.
                        real_start=min(max(started_at or now, contest.start_time), now))
                    transaction.on_commit(contest.update_user_count, robust=True)
                filed = []
                for answer in to_file:
                    lock.renew()
                    question_id = answer['question_id']
                    contest_problem = by_question[question_id]
                    prepared = _answer_for(contest, profile, contest_problem, question_id, answer, warnings)
                    if prepared is None:
                        continue
                    language, value = prepared
                    submission = _file_answer(profile, contest_problem, participation, language, value, question_id)
                    stored += _stored_files(submission)
                    filed.append({'question_id': question_id, 'submission_id': submission.id})
                record = {
                    'attempt_id': attempt_id, 'receipt': 'oj_rcpt_' + secrets.token_hex(12),
                    'contest_id': contest.id, 'profile_id': profile.id, 'participation_id': participation.id,
                    'body_sha256': body_hash, 'package_version': str(payload.get('package_version') or '')[:32],
                    'submissions': filed, 'warnings': warnings, 'started_at': iso(started_at),
                    'deadline_at': iso(deadline_at), 'handed_in_at': iso(handed_in_at), 'received_at': iso(now),
                    'handed_in_evidence': evidence, 'report_url': report_url,
                    'is_late': _lateness_time(contest, handed_in_at, now) > closes_at + LATE_GRACE,
                }
                # Written before the commit, so a delivery waiting on the profile lock finds it.
                cache.set_many(dict.fromkeys(keys, record), RECORD_TTL)
                written = True
        except BaseException as e:
            _delete_stored(stored)
            if written:
                cache.delete_many(keys)
            if isinstance(e, TRANSIENT_ERRORS):
                logger.exception('FairPlay hand-in of %s for %s failed', profile.user.username, contest.key)
                raise transient_error()
            raise
    if warnings:
        logger.warning('FairPlay hand-in of %s for %s filed with warnings: %s', profile.user.username,
                       contest.key, '; '.join(warnings))
    return _receipt(record)


def _file_answer(profile, contest_problem, participation, language, value, question_id):
    try:
        return create_submission(
            profile, contest_problem.problem, language=language, answer=value,
            participation=participation, contest_problem=contest_problem, judge_on_commit=True)
    except TRANSIENT_ERRORS:
        raise
    except (ValidationError, ValueError, TypeError, KeyError) as e:
        raise DeliveryError(422, 'Could not store this answer: %s' % e.__class__.__name__, question_id)


def _lateness_time(contest, handed_in_at, now):
    """FairPlay's hand-in time, kept between the exam's start and DKU OJ's receive time."""
    if handed_in_at is None:
        return now
    return min(max(handed_in_at, contest.start_time), now)


_FINAL_STATUSES = ('D', 'IE', 'CE', 'AB')


def _handed_in(contest):
    staff = set(contest.editor_ids) | set(contest.tester_ids)
    return list(contest.users.filter(virtual=ContestParticipation.LIVE).exclude(user_id__in=staff)
                .select_related('user__user').order_by('id'))


def _result_state(participation, contest_problems, scoring):
    """What the student may see now: released points per question, or ``{'released': False}``."""
    from judge.utils.contest_grade import student_display_points, student_display_released
    user = participation.user.user
    questions = []
    total = 0
    for cp in contest_problems:
        submission = scoring.get((participation.id, cp.id))
        if submission is None:
            questions.append({'question_id': problem_question_id(cp.problem_id), 'points': 0, 'feedback_md': ''})
            continue
        if submission.status not in _FINAL_STATUSES or not submission.results_visible_to(user):
            return {'released': False}
        points = student_display_points(submission) or 0
        total += points
        questions.append({
            'question_id': problem_question_id(cp.problem_id),
            'points': points,
            'feedback_md': (submission.review_note or '') if student_display_released(submission) else '',
        })
    return {'released': True, 'total': total, 'total_points': sum(cp.points for cp in contest_problems),
            'questions': questions}


def refresh_results(contest, now=None, update=True):
    """A result with no cached digest counts as changed, so a withdrawn release is never missed."""
    from judge.utils.contest_grade import scoring_submissions_for_contest
    now = now or timezone.now()
    participations = _handed_in(contest)
    if not participations:
        return {}
    contest_problems = list(contest.contest_problems.order_by('order', 'id'))
    scoring = scoring_submissions_for_contest(contest, live_only=True,
                                              participation_ids=[p.id for p in participations])
    key = _KEY_RESULTS % contest.id
    tracked = cache.get(key)
    tracked = dict(tracked) if isinstance(tracked, dict) else {}
    out, changed = {}, False
    for participation in participations:
        state = _result_state(participation, contest_problems, scoring)
        digest = hashlib.sha256(canonical_json(state).encode('utf-8')).hexdigest()
        seen = tracked.get(str(participation.id))
        if not isinstance(seen, dict) or (update and seen.get('d') != digest):
            seen = {'d': digest, 'u': iso(now)}
            tracked[str(participation.id)] = seen
            changed = True
        out[participation.id] = (seen['u'], state, participation)
    if update and changed:
        cache.set(key, tracked, RECORD_TTL)
    return out


def results_page(contest, changed_since, page):
    """One page of released results (and withdrawn releases) changed since ``changed_since``."""
    now = timezone.now()
    # Changes are noted by the first page only, so the later pages of one pass stay in place.
    states = refresh_results(contest, now, update=page == 1)
    rows = []
    for updated_at, state, participation in states.values():
        when = parse_time(updated_at, 'updated_at')
        if changed_since is not None and when <= changed_since:
            continue
        rows.append((when, participation.id, updated_at, state, participation))
    rows.sort(key=lambda row: (row[0], row[1]))
    window = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE + 1]
    has_next = len(window) > PAGE_SIZE
    results = [dict(state, username=participation.user.user.username, updated_at=updated_at)
               for _when, _pid, updated_at, state, participation in window[:PAGE_SIZE]]
    return {'results': results, 'next_page': page + 1 if has_next else None, 'server_time': iso(now)}
