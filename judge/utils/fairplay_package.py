"""What FairPlay downloads for a FairPlay assignment: the exam list entry, the package and its assets."""
import hashlib
import json
import logging
import mimetypes
import posixpath
import re
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, SuspiciousFileOperation
from django.core.files.storage import default_storage
from django.db.models import Q
from django.utils import timezone

from judge.models import Class, Contest, EditorAttachment, ProblemSubmissionType, ProblemTemplate, Profile, \
    contest_problem_display_name
from judge.utils.fairplay import fairplay_exams
from judge.utils.fairplay_teaching import students_only

__all__ = [
    'ASSET_MAX_BYTES', 'EXAM_ASSETS_MAX_BYTES', 'PACKAGE_MAX_BYTES', 'QUESTION_TYPES', 'build_exam_entry',
    'build_package', 'canonical_json', 'code_languages', 'exam_list_page', 'exam_roster', 'extract_samples', 'iso',
    'problem_question_id', 'tracked_exams',
]

logger = logging.getLogger('judge.fairplay')

PACKAGE_MAX_BYTES = 5 * 1024 * 1024
ASSET_MAX_BYTES = 10 * 1024 * 1024
EXAM_ASSETS_MAX_BYTES = 50 * 1024 * 1024
# Assignments are tracked in the exam list for this long after they end.
TRACK_AFTER_END = timedelta(days=30)

QUESTION_TYPES = {
    ProblemSubmissionType.MCQ: 'choice',
    ProblemSubmissionType.FILL_BLANK: 'fill_blank',
    ProblemSubmissionType.SHORT_ANSWER: 'short_answer',
    ProblemSubmissionType.FILE: 'file_upload',
    ProblemSubmissionType.CODE: 'code',
}


def iso(dt):
    """ISO-8601 UTC with a ``Z`` suffix, microseconds only when present (None stays None)."""
    if dt is None:
        return None
    return dt.astimezone(dt_timezone.utc).replace(tzinfo=None).isoformat() + 'Z'


def canonical_json(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def problem_question_id(problem_id):
    return 'p%06d' % problem_id


_HOST = r'(?:https?:)?(?://[^\s/()"\'<>]+)?'
_ATTACHMENT_URL = re.compile(_HOST + r'/widgets/files/(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                             r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?')
_EXT = re.compile(r'^\.[a-z0-9]{1,8}$')


def _media_url_re():
    return re.compile(_HOST + re.escape(settings.MEDIA_URL or '/media/') + r'(?P<path>[^\s()"\'<>?#]+)')


def _ext(name):
    ext = posixpath.splitext(name or '')[1].lower()
    return ext if _EXT.match(ext) else ''


class _Asset:
    def __init__(self, name, sha256, size, opener):
        self.name = name
        self.sha256 = sha256
        self.size = size
        self.content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        self._opener = opener

    def read(self):
        with self._opener() as handle:
            return handle.read()

    def listing(self):
        return {'name': self.name, 'sha256': self.sha256, 'bytes': self.size, 'content_type': self.content_type}


def _hashed(cache_key, size, opener):
    """SHA-256 of a stored file, cached by identity and size (stored objects are not rewritten)."""
    key = 'fpb:asset:%s:%d' % (hashlib.sha1(cache_key.encode()).hexdigest(), size)
    digest = cache.get(key)
    if digest is None:
        hasher = hashlib.sha256()
        with opener() as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        cache.set(key, digest, 7 * 24 * 3600)
    return digest


class _AssetCollector:
    """Resolves asset URLs in statements to stored files, within the per-file and per-exam limits."""

    def __init__(self):
        self.assets = {}
        self.total = 0
        self._media_re = _media_url_re()

    def _add(self, name, cache_key, size, opener):
        if name in self.assets:
            return name
        if size > ASSET_MAX_BYTES or self.total + size > EXAM_ASSETS_MAX_BYTES:
            return None
        try:
            digest = _hashed(cache_key, size, opener)
        except OSError:
            logger.warning('FairPlay package: could not read %s', cache_key, exc_info=True)
            return None
        self.assets[name] = _Asset(name, digest, size, opener)
        self.total += size
        return name

    def _attachment(self, match):
        try:
            attachment = EditorAttachment.objects.get(uuid=match.group('uuid').lower())
        except (EditorAttachment.DoesNotExist, ValueError):
            return None
        if not attachment.file:
            return None
        name = 'att-%s%s' % (attachment.uuid.hex, _ext(attachment.original_name))
        return self._add(name, 'att:' + attachment.file.name, attachment.size or 0,
                         lambda: attachment.file.storage.open(attachment.file.name, 'rb'))

    def _media(self, match):
        path = posixpath.normpath(match.group('path'))
        directory = (getattr(settings, 'MARTOR_UPLOAD_MEDIA_DIR', '') or '').strip('/')
        if not directory or not path.startswith(directory + '/') or '..' in path.split('/') or not _ext(path):
            return None
        try:
            if not default_storage.exists(path):
                return None
            size = default_storage.size(path)
        except (SuspiciousFileOperation, OSError, NotImplementedError):
            return None
        name = 'media-%s%s' % (hashlib.sha256(path.encode()).hexdigest()[:16], _ext(path))
        return self._add(name, 'media:' + path, size, lambda: default_storage.open(path, 'rb'))

    def rewrite(self, text):
        if not text:
            return text

        def substitute(resolver):
            def replace(match):
                name = resolver(match)
                return 'fp-asset:%s' % name if name else match.group(0)
            return replace

        text = _ATTACHMENT_URL.sub(substitute(self._attachment), text)
        return self._media_re.sub(substitute(self._media), text)


_FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')
_HEADING = re.compile(r'^\s{0,3}(?:#{1,6}\s+(?P<h>.+?)\s*#*\s*$|\*\*(?P<b>[^*]+)\*\*\s*:?\s*$)')


def _sample_kind(heading):
    heading = (heading or '').lower()
    if not any(word in heading for word in ('sample', 'example', '样例', '示例', '样本')):
        return None
    if 'input' in heading or '输入' in heading:
        return 'input'
    if 'output' in heading or '输出' in heading:
        return 'output'
    return None


def extract_samples(markdown):
    """Visible samples from a statement: fenced blocks under "Sample Input"/"Sample Output" headings."""
    inputs, outputs = [], []
    heading = None
    lines = (markdown or '').splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            kind = _sample_kind(heading)
            text = '\n'.join(body) + '\n' if body else ''
            if kind == 'input':
                inputs.append(text)
            elif kind == 'output':
                outputs.append(text)
            heading = None
        else:
            match = _HEADING.match(line)
            if match:
                heading = match.group('h') or match.group('b')
        i += 1
    return [{'input': a, 'output': b} for a, b in zip(inputs, outputs)]


def _statement(problem, collector):
    if problem.is_full_markup:
        from judge.jinja2.markdown import markdown
        # Rendered with the bleached problem style, so the HTML is sanitized.
        html = str(markdown(problem.description or '', 'problem'))
        return None, collector.rewrite(html)
    return collector.rewrite(problem.description or ''), None


def _choice(problem, collector):
    try:
        config = problem.mcq_config
    except ObjectDoesNotExist:
        config = None
    options = [{'id': option.id, 'text_md': collector.rewrite(option.text)}
               for option in problem.mcq_options.order_by('order', 'id')]
    return {
        'multiple': bool(config and config.multiple_select),
        'true_false': bool(config and config.is_true_false),
        'shuffle': bool(config and config.shuffle_options),
        'options': options,
    }


def _fill_blank(problem):
    try:
        config = problem.fill_blank_config
    except ObjectDoesNotExist:
        config = None
    blanks = []
    for index, blank in enumerate(problem.blanks.order_by('order', 'id')):
        try:
            points = float(blank.points)
        except (TypeError, ValueError):
            points = 1.0
        blanks.append({'index': index, 'points': points if points > 0 else 1.0})
    return {'blanks': blanks, 'case_sensitive': bool(config and config.case_sensitive)}


def _file_upload(problem, collector):
    from judge.models import ProblemFileConfig
    try:
        config = problem.file_config
    except ObjectDoesNotExist:
        config = None
    return {
        'allowed_extensions': config.allowed_extension_list() if config else [],
        'max_file_size': (config.max_file_size if config else
                          ProblemFileConfig._meta.get_field('max_file_size').get_default()),
        'instructions_md': collector.rewrite(config.instructions) if config else '',
    }


def code_languages(problem):
    """Allowed languages; only those with a harness when the problem has harnesses."""
    languages = problem.allowed_languages.all()
    harnessed = problem.harnesses.values('language_id')
    if problem.harnesses.exists():
        languages = languages.filter(id__in=harnessed)
    return languages


def _code(problem):
    languages = list(code_languages(problem).order_by('key'))
    own = {template.language_id: template.code
           for template in ProblemTemplate.objects.filter(problem=problem, language__in=languages)}
    templates = {}
    for language in languages:
        code = own.get(language.id, language.template or '')
        if code:
            templates[language.key] = code
    return {
        'languages': [{'key': language.key, 'name': language.name} for language in languages],
        'time_limit_s': float(problem.time_limit),
        'memory_limit_kb': int(problem.memory_limit),
        'templates': templates,
        'samples': extract_samples(problem.description),
    }


def _question(contest, contest_problem, index, collector):
    problem = contest_problem.problem
    kind = QUESTION_TYPES.get(problem.submission_type, 'code')
    statement_md, statement_html = _statement(problem, collector)
    try:
        label = str(contest.get_label_for_problem(index))
    except Exception:
        logger.warning('FairPlay package: no label for problem %d of %s', index, contest.key, exc_info=True)
        label = str(index + 1)
    question = {
        'id': problem_question_id(problem.id),
        'order': index + 1,
        'label': label,
        'title': contest_problem_display_name(contest_problem),
        'type': kind,
        'points': contest_problem.points,
        'partial': contest_problem.partial,
        'statement_md': statement_md,
        'statement_html': statement_html,
    }
    if kind == 'choice':
        question['choice'] = _choice(problem, collector)
    elif kind == 'fill_blank':
        question['fill_blank'] = _fill_blank(problem)
    elif kind == 'short_answer':
        question['short_answer'] = {}
    elif kind == 'file_upload':
        question['file_upload'] = _file_upload(problem, collector)
    else:
        question['code'] = _code(problem)
    return question


def build_package(contest):
    """What a student needs to answer and nothing that grades: no correct options, accepted answers or tests."""
    collector = _AssetCollector()
    contest_problems = list(contest.contest_problems.select_related('problem').order_by('order', 'id'))
    questions = [_question(contest, cp, index, collector) for index, cp in enumerate(contest_problems)]
    body = {
        'total_points': sum(cp.points for cp in contest_problems),
        'description_md': collector.rewrite(contest.description or ''),
        'questions': questions,
        'assets': [asset.listing() for asset in collector.assets.values()],
    }
    # Any change to what students receive changes the version.
    package_version = _sha256_hex(canonical_json(dict(body, key=contest.key)).encode('utf-8'))[:16]
    return {'key': contest.key, 'package_version': package_version, **body}, collector.assets


def _person(profile):
    """No email: FairPlay never uses it."""
    user = profile.user
    return {'username': user.username, 'first_name': user.first_name, 'last_name': user.last_name}


def exam_roster(contest):
    """The exam's audience minus non-students (``students_only``), its editors, testers and banned users."""
    if contest.is_organization_private:
        if contest.classes.exists():
            queryset = Profile.objects.filter(class__in=contest.classes.all())
        else:
            queryset = Profile.objects.filter(organizations__in=contest.organizations.all())
        if contest.is_private:
            queryset = queryset.filter(id__in=contest.private_contestants.values('id'))
    elif contest.is_private:
        queryset = Profile.objects.filter(id__in=contest.private_contestants.values('id'))
    else:
        queryset = Profile.objects.none()
    org_ids = set(contest.organizations.values_list('id', flat=True)) | \
        set(contest.classes.values_list('organization_id', flat=True))
    class_ids = set(Class.objects.filter(organization_id__in=org_ids).values_list('id', flat=True))
    also = set(contest.editor_ids) | set(contest.tester_ids) | \
        set(contest.banned_users.values_list('id', flat=True))
    return students_only(queryset, org_ids, class_ids, also)


def _course(contest):
    organization = contest.organizations.order_by('id').first()
    if organization is None:
        first_class = contest.classes.select_related('organization').order_by('id').first()
        organization = first_class.organization if first_class is not None else None
    return {'id': organization.id, 'name': organization.name} if organization is not None else None


def build_exam_entry(contest, package=None):
    if package is None:
        package = build_package(contest)[0]
    editors = Profile.objects.filter(
        Q(authored_contests=contest) | Q(curated_contests=contest)).select_related('user').distinct()
    return {
        'key': contest.key,
        'name': contest.name,
        'course': _course(contest),
        'classes': [{'id': c.id, 'name': c.name} for c in contest.classes.order_by('id')],
        'start_time': iso(contest.start_time),
        'end_time': iso(contest.end_time),
        'time_limit_seconds': int(contest.time_limit.total_seconds()) if contest.time_limit else None,
        'results_hidden': bool(contest.format_class.has_hidden_results and not contest.results_finalized),
        'package_version': package['package_version'],
        'removed': False,
        'editors': [_person(p) for p in sorted(editors, key=lambda p: p.user.username)],
        'roster': [dict(_person(p), extra_time_seconds=0) for p in exam_roster(contest)],
    }


# A change is a new digest of an entry; a digest the cache lost only lists the entry again.

_KEY_TRACK = 'fpb:exam-track:%d'
TRACK_TTL = int((TRACK_AFTER_END + timedelta(days=30)).total_seconds())


def tracked_exams(now=None):
    """Visible (a hidden assignment is a draft) and not ended more than ``TRACK_AFTER_END`` ago."""
    now = now or timezone.now()
    return fairplay_exams(Contest.objects.filter(is_visible=True, end_time__gte=now - TRACK_AFTER_END))


def _refresh(contests, now, update):
    tracked = cache.get_many([_KEY_TRACK % c.id for c in contests])
    times, entries, changed = {}, {}, {}
    for contest in contests:
        seen = tracked.get(_KEY_TRACK % contest.id)
        if update:
            entry = build_exam_entry(contest)
            entries[contest.id] = entry
            digest = _sha256_hex(canonical_json(entry).encode('utf-8'))
            if not isinstance(seen, dict) or seen.get('d') != digest:
                seen = {'d': digest, 'u': iso(now)}
                changed[_KEY_TRACK % contest.id] = seen
        elif not isinstance(seen, dict):
            seen = {'u': iso(now)}
        times[contest.id] = seen['u']
    if changed:
        cache.set_many(changed, TRACK_TTL)
    return times, entries


def exam_list_page(changed_since, page, page_size, now=None):
    """Entries changed since ``changed_since``, oldest first; only page 1 records changes."""
    from judge.utils.fairplay_delivery import parse_time
    now = now or timezone.now()
    contests = list(tracked_exams(now).order_by('id'))
    times, entries = _refresh(contests, now, update=page == 1)
    rows = []
    for contest in contests:
        when = parse_time(times[contest.id], 'updated_at')
        if changed_since is not None and when <= changed_since:
            continue
        rows.append((when, contest.key, contest))
    rows.sort(key=lambda row: (row[0], row[1]))
    window = rows[(page - 1) * page_size:page * page_size + 1]
    has_next = len(window) > page_size
    result = []
    for _when, _key, contest in window[:page_size]:
        entry = entries.get(contest.id) or build_exam_entry(contest)
        result.append(dict(entry, updated_at=times[contest.id]))
    body = {'exams': result, 'next_page': page + 1 if has_next else None, 'server_time': iso(now)}
    if page == 1:
        body['active_keys'] = sorted(contest.key for contest in contests)
        body['active_since'] = iso(now - TRACK_AFTER_END)
    return body
