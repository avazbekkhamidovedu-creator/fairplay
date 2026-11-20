"""FairPlay bridge API (``/api/fairplay/v1/``), called by the FairPlay server only."""
import json
import logging
from functools import wraps
from django.core.exceptions import ImproperlyConfigured

from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from judge.models import Class, Contest, Profile
from judge.utils.fairplay import FAIRPLAY_LEAD, fairplay_delivery_exams, fairplay_exams, fairplay_token
from judge.utils.fairplay_delivery import DeliveryError, TRANSIENT_ERRORS, deliver_submission, parse_time, \
    results_page, store_upload, transient_error
from judge.utils.fairplay_package import PACKAGE_MAX_BYTES, build_package, exam_list_page, iso
from judge.utils.fairplay_signing import SignatureError, parse_signature_headers, signed_headers, verify_signature
from judge.utils.fairplay_identity import installation_id
from judge.utils.fairplay_teaching import class_roster, classes_with_fairplay_login

__all__ = ['class_roster_view', 'exam_asset', 'exam_package', 'exam_results', 'exam_submissions', 'exam_uploads',
           'exams', 'student_classes_view']

logger = logging.getLogger('judge.fairplay')

PAGE_SIZE = 100
SUBMISSION_MAX_BYTES = 2 * 1024 * 1024
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_BYTES = 64 * 1024


def _error(status, detail, **extra):
    return JsonResponse(dict({'detail': detail}, **extra), status=status, json_dumps_params={'ensure_ascii': False})


def _json(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def _bad_signature():
    return _error(401, 'bad signature')


def _delivery_error(error):
    response = _json(error.body, status=error.status)
    if error.retry_after:
        response['Retry-After'] = str(int(error.retry_after))
    return response


def _too_large(limit):
    return DeliveryError(413, 'Request body is larger than %d bytes.' % limit)


def _check_length(request, limit):
    """Refuse a declared ``Content-Length`` over ``limit`` before anything of the body is read."""
    try:
        declared = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise DeliveryError(400, 'Invalid Content-Length.')
    if declared > limit:
        raise _too_large(limit)


def _read_body(request, limit):
    data = request.read(limit + 1)
    if len(data) > limit:
        raise _too_large(limit)
    return data


def fairplay_api(view=None, max_bytes=DEFAULT_MAX_BYTES):
    """Signature check; 404 while the bridge is off; headers and length are checked before the body is read."""
    if view is None:
        return lambda real: fairplay_api(real, max_bytes=max_bytes)

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not fairplay_token():
            raise Http404()
        request.no_profile_update = True
        try:
            parsed = parse_signature_headers(request)
            _check_length(request, max_bytes)
            body = _read_body(request, max_bytes)
            verify_signature(request, body, parsed)
        except SignatureError:
            return _bad_signature()
        except DeliveryError as e:
            return _delivery_error(e)
        request.fairplay_body = body
        try:
            identity = installation_id()
            if request.GET.getlist('installation_id') != [identity]:
                response = _error(409, 'Installation identity does not match.')
            elif request.content_type == 'application/json' and body and isinstance(json.loads(body), dict) and \
                    json.loads(body).get('installation_id', identity) != identity:
                response = _error(409, 'Installation identity does not match.')
            else:
                response = view(request, *args, **kwargs)
        except ImproperlyConfigured:
            response = _error(503, 'Installation identity is not configured.')
        except (ValueError, UnicodeDecodeError):
            response = _error(400, 'Invalid JSON body.')
        except Http404:
            response = _error(404, 'Not Found')
        except DeliveryError as e:
            response = _delivery_error(e)
        except TRANSIENT_ERRORS:
            logger.exception('FairPlay API %s %s failed', request.method, request.path)
            response = _delivery_error(transient_error('DKU OJ is temporarily unavailable; try again.'))
        if not response.streaming:
            for name, value in signed_headers('RESPONSE %s %d' % (request.method, response.status_code),
                                             request.get_full_path(), response.content,
                                             acting_user=parsed[1]).items():
                response[name] = value
        return response
    return csrf_exempt(wrapped)


def _page(request):
    try:
        page = int(request.GET.get('page') or 1)
    except ValueError:
        raise DeliveryError(400, 'page must be a positive integer.')
    if page < 1:
        raise DeliveryError(400, 'page must be a positive integer.')
    return page


def _changed_since(request):
    try:
        return parse_time(request.GET.get('changed_since'), 'changed_since')
    except DeliveryError:
        raise DeliveryError(400, 'changed_since must be an ISO-8601 time.')


def _exam(key, delivery=False):
    contest = Contest.get_by_key(key, fairplay_delivery_exams() if delivery else fairplay_exams())
    if contest is None:
        raise DeliveryError(404, 'No FairPlay exam with this key.')
    return contest


def _not_modified(request, etag):
    if etag not in [tag.strip() for tag in request.META.get('HTTP_IF_NONE_MATCH', '').split(',')]:
        return None
    response = HttpResponse(status=304)
    response['ETag'] = etag
    return response


def _too_early(contest, now):
    opens = contest.start_time - FAIRPLAY_LEAD
    if now >= opens:
        return None
    response = _error(425, 'The exam package is available from %s.' % iso(opens))
    response['Retry-After'] = str(int((opens - now).total_seconds()) + 1)
    return response


@fairplay_api
@require_GET
def installation(request):
    return _json({'protocol': 1, 'installation_id': installation_id(),
                  'request_nonce': request.META['HTTP_X_FP_NONCE']})


@fairplay_api
@require_GET
def exams(request):
    return _json(exam_list_page(_changed_since(request), _page(request), PAGE_SIZE, timezone.now()))


@fairplay_api
@require_GET
def exam_package(request, key):
    contest = _exam(key)
    early = _too_early(contest, timezone.now())
    if early is not None:
        return early
    package, _assets = build_package(contest)
    etag = '"%s"' % package['package_version']
    not_modified = _not_modified(request, etag)
    if not_modified is not None:
        return not_modified
    body = json.dumps(package, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) > PACKAGE_MAX_BYTES:
        return _error(422, 'The exam package is larger than 5 MB; reduce the statements or images.')
    response = HttpResponse(body, content_type='application/json; charset=utf-8')
    response['ETag'] = etag
    response['Cache-Control'] = 'no-store'
    return response


@fairplay_api
@require_GET
def exam_asset(request, key, name):
    contest = _exam(key)
    early = _too_early(contest, timezone.now())
    if early is not None:
        return early
    _package, assets = build_package(contest)
    asset = assets.get(name)
    if asset is None:
        return _error(404, 'No such asset in the current package.')
    etag = '"%s"' % asset.sha256
    not_modified = _not_modified(request, etag)
    if not_modified is not None:
        return not_modified
    try:
        data = asset.read()
    except TRANSIENT_ERRORS:
        logger.warning('FairPlay asset %s of %s could not be read', name, key, exc_info=True)
        return _delivery_error(transient_error('The asset store is temporarily unavailable.'))
    response = HttpResponse(data, content_type=asset.content_type)
    response['ETag'] = etag
    response['Cache-Control'] = 'no-store'
    return response


@fairplay_api(max_bytes=UPLOAD_MAX_BYTES)
@require_POST
def exam_uploads(request, key):
    # The signature covers the query string; X-Sha256 is checked against the signed body.
    contest = _exam(key, delivery=True)
    data = request.fairplay_body
    upload_id = store_upload(
        contest,
        username=request.GET.get('username', ''),
        question_id=request.GET.get('question_id', ''),
        filename=request.GET.get('filename', ''),
        sha256=request.META.get('HTTP_X_SHA256', ''),
        data=data,
    )
    return _json({'upload_id': upload_id})


@fairplay_api(max_bytes=SUBMISSION_MAX_BYTES)
@require_POST
def exam_submissions(request, key):
    contest = _exam(key, delivery=True)
    data = request.fairplay_body
    try:
        payload = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return _error(400, 'Body must be UTF-8 JSON.')
    return _json(deliver_submission(contest, payload))


@fairplay_api
@require_GET
def exam_results(request, key):
    contest = _exam(key)
    return _json(results_page(contest, _changed_since(request), _page(request)))


def fairplay_classes():
    """Classes a FairPlay exam uses: in ``classes`` of one, or of a course in its ``organizations``."""
    exams = fairplay_exams()
    return Class.objects.filter(Q(id__in=exams.values('classes')) |
                                Q(organization_id__in=exams.values('organizations')))


def fairplay_class_ids(class_ids):
    """Only classes used by a FairPlay exam or given a class login here, even for a signed call."""
    class_ids = set(class_ids)
    if not class_ids:
        return set()
    allowed = set(fairplay_classes().filter(id__in=class_ids).values_list('id', flat=True))
    return allowed | classes_with_fairplay_login(class_ids - allowed)


@fairplay_api
@require_GET
def class_roster_view(request, class_id):
    """Fetched by FairPlay at student set-up; 404 for a class it may not ask about."""
    klass = Class.objects.select_related('organization').filter(id=class_id).first()
    if klass is None or klass.id not in fairplay_class_ids([klass.id]):
        return _error(404, 'No class with this id.')
    return _json({
        'class': {'id': klass.id, 'name': klass.name},
        'course': {'id': klass.organization.id, 'name': klass.organization.name},
        'students': [{'username': p.user.username, 'first_name': p.user.first_name, 'last_name': p.user.last_name}
                     for p in class_roster(klass)],
    })


def _class_teachers(klass):
    """Instructor names, else the class admins, else the course administrators."""
    names = [n.strip() for n in (klass.instructor_names or '').split(',') if n.strip()]
    if names:
        return names
    for profiles in (klass.admins.all(), klass.organization.admins.all()):
        names = [(('%s %s' % (p.user.first_name, p.user.last_name)).strip() or p.user.username)
                 for p in profiles.select_related('user').order_by('user__last_name', 'user__username')]
        if names:
            return names
    return []


@fairplay_api
@require_GET
def student_classes_view(request, netid):
    """Active classes the NetID is a student of, as on the roster, among those FairPlay may ask about."""
    profile = Profile.objects.filter(user__username=netid).select_related('user').first()
    out = []
    if profile is not None:
        classes = list(Class.objects.filter(members=profile, is_active=True).select_related('organization')
                       .distinct().order_by('organization__name', 'name'))
        allowed = fairplay_class_ids(klass.id for klass in classes)
        for klass in classes:
            if klass.id not in allowed:
                continue
            if not class_roster(klass).filter(id=profile.id).exists():
                continue
            out.append({'id': klass.id, 'name': klass.name,
                        'course': {'id': klass.organization.id, 'name': klass.organization.name},
                        'teachers': _class_teachers(klass)})
    response = _json({'classes': out})
    response['Cache-Control'] = 'no-store'
    return response
