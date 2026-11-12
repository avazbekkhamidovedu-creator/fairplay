"""FairPlay in the assignment editor and on the assignment page; the exam settings are stored in FairPlay."""
import logging
import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from judge.utils.fairplay import FAIRPLAY_CALCULATOR_CHOICES, FAIRPLAY_REVIEW_CHOICES, fairplay_check_contest, \
    fairplay_edit_locked, fairplay_enabled, is_fairplay_exam, set_fairplay_exam
from judge.utils.fairplay_audience import audience_descriptor, confirm_audience

__all__ = [
    'DEFAULT_SETTINGS', 'FAIRPLAY_EDITOR_FIELDS', 'FairPlayEditLock', 'FairPlayEditorPlan', 'FairPlaySaveResult',
    'BOX_CHANGE_REFUSED', 'apply_fairplay_editor', 'clean_settings', 'editor_settings', 'fairplay_check_edit',
    'fairplay_box_error', 'fairplay_edit_lock', 'fairplay_editor_context', 'fairplay_save_settings',
    'parse_fairplay_payload', 'push_settings', 'websites_text',
]

logger = logging.getLogger('judge.fairplay')

# Editor fields gated by the same permission as the Safe Exam Browser option (contest_seb_requirement).
FAIRPLAY_EDITOR_FIELDS = ('fairplay_required', 'fairplay_settings', 'fairplay_audience_confirm', 'fairplay_audience_mode')
# What FairPlay uses until settings are saved.
DEFAULT_SETTINGS = {
    'calculator': 'off', 'open_book': False, 'websites': [], 'strong_lock': False,
    'resume_needs_approval': True, 'upload_opens_days': 0, 'review': 'nothing',
}
MAX_WEBSITE_LINES = 100
MAX_WEBSITE_LENGTH = 2100
MAX_UPLOAD_DAYS = 7
_AUDIENCE_FIELDS = {'classes', 'organizations', 'private_contestants', 'is_private', 'is_organization_private'}
BOX_LINK_RE = re.compile(r'https://duke(?:\.app)?\.box\.com/s/[A-Za-z0-9_-]+', re.IGNORECASE)
BOX_CHANGE_REFUSED = _('The Duke Box folder cannot be changed after students have started.')


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
    if 'box_link' in raw:
        link = raw['box_link']
        if link is None:
            link = ''
        if not isinstance(link, str):
            raise ValidationError(_('Duke Box shared folder link: use a link like https://duke.box.com/s/...'))
        link = link.strip()
        if link and not BOX_LINK_RE.fullmatch(link):
            raise ValidationError(_('Duke Box shared folder link: use a shared link like '
                                    'https://duke.box.com/s/... or https://duke.app.box.com/s/...'))
        out['box_link'] = link
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
    if isinstance(fp_settings.get('box_link'), str) and fp_settings['box_link'].strip():
        out['box_link'] = fp_settings['box_link'].strip()
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


class FairPlayEditorPlan:
    """The lock on or off (None: unchanged) and the settings to push after the save."""

    def __init__(self, want=None, settings=None, was_on=False):
        self.want, self.settings, self.was_on = want, settings, was_on
        self.audience_mode = None
        self.audience_from_scope = False


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
        plan.settings = clean_settings(payload['fairplay_settings'], partial=True)
    can_reconcile = not (_AUDIENCE_FIELDS & set(restricted))
    plan.audience_from_scope = bool((_AUDIENCE_FIELDS | {'vis_mode'}) & payload.keys()) and can_reconcile
    if payload.get('fairplay_audience_confirm') is True:
        if 'fairplay_audience_confirm' in restricted or not can_reconcile:
            raise ValidationError('You cannot confirm this FairPlay audience.')
        mode = payload.get('fairplay_audience_mode')
        if mode not in ('classes', 'organizations', 'selected'):
            raise ValidationError('Choose the intended FairPlay audience before confirming it.')
        plan.audience_mode = mode
    return plan


def apply_fairplay_editor(contest, plan):
    """Runs after the many-to-many save, since the tag picker leaves the FairPlay tag out."""
    on = plan.was_on if plan.want is None else plan.want
    set_fairplay_exam(contest, on)
    fairplay_check_contest(contest)
    if on and plan.audience_from_scope:
        mode = ('classes' if contest.is_organization_private and contest.classes.exists() else
                'organizations' if contest.is_organization_private else 'selected')
        confirm_audience(contest, mode)
    elif on and plan.audience_mode is not None:
        confirm_audience(contest, plan.audience_mode)


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


def acting_editor_username(contest):
    """A synced editor of the exam (author first, then curator), for reads made without a request user."""
    from judge.models import Profile
    for relation in ('authored_contests', 'curated_contests'):
        name = Profile.objects.filter(**{relation: contest}).order_by('user__username').values_list(
            'user__username', flat=True).first()
        if name:
            return name
    return None


CLOSED_BOOK = {'pages': [], 'resources': []}
_ORIGIN_RE = re.compile(r'https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?')
_WILDCARD_RE = re.compile(r'https://\*\.([a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+)')
_SHARED_SUFFIXES = frozenset((
    'com', 'org', 'net', 'edu', 'gov', 'mil', 'int', 'io', 'co', 'ac', 'app', 'dev', 'info', 'biz',
    'co.uk', 'ac.uk', 'org.uk', 'gov.uk', 'me.uk', 'com.au', 'edu.au', 'org.au', 'net.au', 'com.cn', 'edu.cn',
    'org.cn', 'net.cn', 'gov.cn', 'co.jp', 'ac.jp', 'co.kr', 'ac.kr', 'co.in', 'ac.in', 'com.br', 'com.mx',
    'co.nz', 'co.za', 'edu.uz', 'com.uz', 'github.io', 'herokuapp.com', 'blogspot.com', 'appspot.com',
    'cloudfront.net', 'amazonaws.com', 'azurewebsites.net', 'netlify.app', 'vercel.app', 'pages.dev',
    's3.amazonaws.com', 'workers.dev', 'wordpress.com', 'githubusercontent.com', 'gitlab.io', 'onrender.com',
    'fly.dev', 'replit.dev', 'r2.dev', 'hf.space', 'web.app', 'firebaseapp.com', 'notion.site', 'glitch.me', 'repl.co'))


def _resource_ok(value):
    if not isinstance(value, str) or len(value) > 300:
        return False
    if _ORIGIN_RE.fullmatch(value):
        return True
    m = _WILDCARD_RE.fullmatch(value)
    return bool(m) and m.group(1) not in _SHARED_SUFFIXES
MAX_SIGNED_SITES = 32


def clean_lock_sites(value):
    """FairPlay's lock_sites reduced to what may be signed: https pages, https origins, no resources without pages."""
    if not isinstance(value, dict):
        return dict(CLOSED_BOOK, pages=[], resources=[])
    pages = [p for p in value.get('pages') or [] if isinstance(p, str) and p.startswith('https://') and '*' not in p.split('/')[2]
             and len(p) <= 2100 and not any(c in p for c in ' \t\r\n"\'<>')]
    resources = [r for r in value.get('resources') or [] if _resource_ok(r)]
    if not pages:
        return {'pages': [], 'resources': []}
    resources = resources[:max(0, MAX_SIGNED_SITES - 1)]
    return {'pages': pages[:MAX_SIGNED_SITES - len(resources)], 'resources': resources}


OPEN_EXAM_FAILURE = 5


def open_exam_values(contest, acting_user=None, with_lock_sites=False):
    """The saved allowed websites and Duke Box link for the form, and FairPlay's lock_sites (the only
    thing ever signed for a client); empty when FairPlay cannot say."""
    from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
    empty = {'websites': [], 'box_link': ''}
    if with_lock_sites:
        empty['lock_sites'] = clean_lock_sites(None)
    from django.core.cache import cache
    failed = 'fp-open-exam-failed:%s' % contest.key
    if cache.get(failed):
        return empty
    try:
        view = FairPlayClient(acting_user=acting_user).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
    except FairPlayError:
        cache.set(failed, 1, OPEN_EXAM_FAILURE)
        return empty
    fp_settings = view.get('settings') if isinstance(view, dict) else None
    settings = editor_settings(fp_settings)
    values = {'websites': [line for line in settings['websites'].splitlines() if line.strip()],
              'box_link': settings.get('box_link', '')}
    if with_lock_sites:
        values['lock_sites'] = clean_lock_sites(fp_settings.get('lock_sites') if isinstance(fp_settings, dict) else None)
    return values


LOCK_SITES_FRESH = 15
LOCK_SITES_FAILURE = 2
LOCK_SITES_STALE = 600
# A waiter covers the fetch's own worst case (connect + read timeout of the quick read, see _fetch_lock_sites) plus a
# margin, so an empty list is never signed only because a fetch was slow. It still ends the moment the fetch finishes.
LOCK_SITES_WAIT = 6.5
LOCK_SITES_FLIGHT_TTL = 10  # comfortably above the fetch worst case (6 s); waiters bound their own wait
LOCK_SITES_MAX_WAITERS = 8  # per exam: a burst of claims cannot park every worker
LOCK_SITES_POLL = 0.2
LOCK_SITES_LOGGER = logging.getLogger('judge.fairplay')


def _fetch_lock_sites(contest, acting_user):
    """FairPlay's lock_sites for the exam, or None when FairPlay cannot be asked or does not answer."""
    from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
    try:
        view = FairPlayClient(acting_user=acting_user).exam(contest.key, read_timeout=QUICK_READ_TIMEOUT)
    except FairPlayError:
        return None
    fp_settings = view.get('settings') if isinstance(view, dict) else None
    if not isinstance(fp_settings, dict):
        return None
    return clean_lock_sites(fp_settings.get('lock_sites'))


def refresh_lock_sites(contest, wait=False):
    """Fill the per-exam cache; call it before any row lock is taken. One caller asks FairPlay at a time, the last
    good value is served for 10 minutes when FairPlay fails, and a failure is remembered for 2 seconds only.
    Only a caller with wait=True (the claim and recover paths, which sign the first state of an exam) waits for a fetch
    already in flight; renew and state heartbeats never do."""
    from django.core.cache import cache
    fresh, good, flight = ('fp-browser-sites-v2:%s' % contest.pk, 'fp-lock-sites-good:%s' % contest.pk,
                           'fp-lock-sites-flight:%s' % contest.pk)
    if isinstance(cache.get(fresh), dict):
        return
    import uuid
    token = uuid.uuid4().hex  # the flight key is owned: only its owner removes it
    if not cache.add(flight, token, LOCK_SITES_FLIGHT_TTL):
        # Another request is already asking FairPlay. With a last-good value cached, signed_lock_sites serves it and
        # nobody waits. Only a request with nothing good to serve waits for the answer, so a first claim never signs an
        # empty (closed-book) list just because a fetch was in flight. Bounded; never fails the caller. (A failed fetch
        # leaves a 2 s answer in the fresh key, which returns above before anything waits.)
        if not wait or isinstance(cache.get(good), dict):
            return
        import time
        waiters = 'fp-lock-sites-waiters:%s' % contest.pk
        ttl = int(LOCK_SITES_WAIT) + 2
        cache.add(waiters, 0, ttl)
        try:
            parked = cache.incr(waiters)
            cache.touch(waiters, ttl)  # every arrival renews the count, so it outlives the waiters it counts
        except ValueError:  # the entry expired between add and incr: start the count again
            cache.set(waiters, 1, ttl)
            parked = 1
        try:
            if parked <= LOCK_SITES_MAX_WAITERS:
                deadline = time.monotonic() + LOCK_SITES_WAIT
                while time.monotonic() < deadline:
                    if isinstance(cache.get(fresh), dict) or not cache.get(flight):
                        break
                    time.sleep(LOCK_SITES_POLL)
            # beyond the cap the caller signs the current value without waiting
        finally:
            try:
                if cache.decr(waiters) < 0:
                    cache.set(waiters, 0, ttl)  # an expired and recreated count never goes below zero
            except ValueError:
                pass
        return
    try:
        editor = acting_editor_username(contest)
        value = _fetch_lock_sites(contest, editor) if editor else None
        if editor is None and cache.add('fp-lock-sites-noeditor:%s' % contest.pk, 1, 3600):
            LOCK_SITES_LOGGER.warning('FairPlay exam %s has no author or curator to read its websites as; '
                                      'students get a closed-book exam', contest.key)
        if value is not None:
            cache.set(fresh, value, LOCK_SITES_FRESH)
            from django.utils import timezone
            until_end = int((contest.end_time - timezone.now()).total_seconds())
            cache.set(good, value, max(LOCK_SITES_STALE, until_end))
        else:
            kept = cache.get(good)
            cache.set(fresh, kept if isinstance(kept, dict) else clean_lock_sites(None), LOCK_SITES_FAILURE)
    finally:
        if cache.get(flight) == token:
            cache.delete(flight)


_ASSET_DENY_SUFFIX = (
    "openai.com", "chatgpt.com", "oaiusercontent.com", "claude.ai", "anthropic.com", "gemini.google.com",
    "bard.google.com", "aistudio.google.com", "notebooklm.google.com", "copilot.microsoft.com",
    "copilot.cloud.microsoft", "bing.com", "perplexity.ai", "poe.com", "deepseek.com", "kimi.moonshot.cn", "kimi.com",
    "moonshot.cn", "doubao.com", "tongyi.aliyun.com", "qianwen.aliyun.com", "yiyan.baidu.com", "chatglm.cn",
    "yuanbao.tencent.com", "character.ai", "you.com", "phind.com", "mistral.ai", "groq.com", "x.ai", "grok.com",
    "meta.ai", "pi.ai", "cohere.com", "huggingface.co", "hf.co", "githubcopilot.com", "copilot.github.com",
    "blackbox.ai", "chatbot.theb.ai", "quillbot.com", "qwen.ai", "tongyi.com", "z.ai", "metaso.cn", "kimi.ai",
    "manus.im", "felo.ai", "chegg.com", "wolframalpha.com", "grammarly.com", "deepl.com", "duckduckgo.com",
    "kagi.com", "msn.com", "github.dev", "vscode.dev", "replit.com", "codepen.io", "jsfiddle.net", "ideone.com",
    "onlinegdb.com", "godbolt.org", "rentry.co", "dpaste.org", "whatsapp.com", "whatsapp.net", "telegram.org",
    "telegram.me", "t.me", "wechat.com", "weixin.qq.com", "wx.qq.com", "qq.com", "messenger.com", "facebook.com",
    "instagram.com", "discord.com", "discord.gg", "discordapp.com", "slack.com", "teams.microsoft.com",
    "teams.live.com", "signal.org", "line.me", "skype.com", "mail.google.com", "outlook.live.com",
    "outlook.office.com", "chat.google.com", "x.com", "twitter.com", "outlook.office365.com", "teams.cloud.microsoft",
    "m365.cloud.microsoft", "zoom.us", "reddit.com", "drive.google.com", "docs.google.com", "sheets.google.com",
    "slides.google.com", "keep.google.com", "dropbox.com", "onedrive.live.com", "1drv.ms", "sharepoint.com",
    "box.com", "notion.so", "notion.site", "evernote.com", "pastebin.com", "paste.ee", "hastebin.com",
    "gist.github.com", "gist.githubusercontent.com", "raw.githubusercontent.com", "wetransfer.com", "mega.nz",
    "pan.baidu.com", "weiyun.com", "kdocs.cn", "docs.qq.com", "shimo.im", "yuque.com", "feishu.cn", "larksuite.com",
    "codeshare.io", "file.io", "gofile.io", "catbox.moe", "notion.com", "icloud.com", "proton.me", "alipan.com",
    "quark.cn",
)
_ASSET_DENY_EXACT = (
    "github.com", "www.github.com", "www.google.com", "google.com", "translate.google.com", "meet.google.com",
    "search.brave.com",
)


def _asset_denied(host):
    return host in _ASSET_DENY_EXACT or any(host == d or host.endswith('.' + d) for d in _ASSET_DENY_SUFFIX)


def _asset_origin(value):
    """'https://host[:port]' for an absolute or protocol-relative URL, else None. Never from user input."""
    from urllib.parse import urlsplit
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith('//'):
        text = 'https:' + text
    if not text.startswith('https://'):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or '').lower().rstrip('.')
    if parts.username is not None or parts.password is not None or '*' in host or not host or _asset_denied(host):
        return None
    origin = 'https://' + host + (':%d' % port if port not in (None, 443) else '')
    return origin if _ORIGIN_RE.fullmatch(origin) else None


_ASSET_TEMPLATE_RE = re.compile(r'<(?:script|link)\b[^>]*?\b(?:src|href)=["\'](https?://[^"\']+|//[^"\']+)["\']', re.I)
_ASSET_TEMPLATES = ('mathjax-load.html', 'base.html', 'problem/workspace.html', 'contest/contest.html', 'problem/problem.html')
_ASSET_SETTINGS = ('ACE_URL', 'SELECT2_JS_URL', 'SELECT2_CSS_URL', 'JQUERY_JS', 'FONTAWESOME_CSS', 'STATIC_URL',
                   'MATHOID_URL', 'MATHOID_CACHE_URL', 'MEDIA_URL')
MAX_ASSETS = 16


def _oj_asset_origins():
    """Exact https origins the OJ's own pages load subresources from (CDNs, remote static), from the live settings
    and the page templates; relative or local URLs add nothing. Deny-listed hosts never appear."""
    import os
    from django.conf import settings
    from django.core.cache import cache
    cached = cache.get('fp-oj-assets')
    if isinstance(cached, list):
        return cached
    candidates = [getattr(settings, name, None) for name in _ASSET_SETTINGS]
    roots = []
    for engine in getattr(settings, 'TEMPLATES', []):
        roots += [d for d in engine.get('DIRS', []) if isinstance(d, str)]
    for name in _ASSET_TEMPLATES:
        for root in roots:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                try:
                    with open(path, encoding='utf-8') as handle:
                        candidates += _ASSET_TEMPLATE_RE.findall(handle.read())
                except (OSError, ValueError):
                    pass
                break
    out = []
    for candidate in candidates:
        origin = _asset_origin(candidate)
        if origin and origin not in out:
            out.append(origin)
    out = out[:MAX_ASSETS]
    cache.set('fp-oj-assets', out, LOCK_SITES_FRESH)
    return out


def _log_hourly(key, message, *args):
    from django.core.cache import cache
    try:
        if cache.add('fp-editor-warn:' + key, 1, 3600):
            LOCK_SITES_LOGGER.exception(message, *args)
    except Exception:
        pass


def oj_asset_origins():
    """The asset origins, never able to raise into a request: on any error the last good list (or none)."""
    from django.core.cache import cache
    try:
        origins = _oj_asset_origins()
        cache.set('fp-oj-assets-good', origins, 86400)
        return origins
    except Exception:
        _log_hourly('assets', 'FairPlay could not work out the OJ asset origins')
        try:
            good = cache.get('fp-oj-assets-good')
        except Exception:
            good = None
        return good if isinstance(good, list) else []


def signed_lock_sites(contest):
    """Pages and resource origins to sign for an active session of this exam. Reads the cache only: it never
    calls FairPlay, so it is safe under row locks; refresh_lock_sites fills the cache beforehand."""
    from django.core.cache import cache
    value = cache.get('fp-browser-sites-v2:%s' % contest.pk)
    if not isinstance(value, dict):
        value = cache.get('fp-lock-sites-good:%s' % contest.pk)
    return clean_lock_sites(value)


def fairplay_editor_context(request, contest, restricted):
    on = is_fairplay_exam(contest) if contest is not None else False
    context = {'fairplay_offered': fairplay_enabled() or on, 'fairplay_on': on, 'fairplay_open_exam': None}
    context['fairplay_audience'] = audience_descriptor(contest) if contest is not None else None
    if fairplay_enabled() and 'fairplay_settings' not in restricted:
        context['fairplay_open_exam'] = (open_exam_values(contest, request.user.username) if on
                                         else {'websites': [], 'box_link': ''})
    return context


class FairPlaySaveResult:

    def __init__(self, warnings=(), not_saved=None, settings=None, status=None):
        self.warnings, self.not_saved, self.settings, self.status = list(warnings), not_saved, settings, status


def fairplay_box_error(result):
    """The message for a refused Duke Box link, or None for any other outcome."""
    if result.not_saved is None or not (result.settings and 'box_link' in result.settings):
        return None
    if result.status == 409:
        return BOX_CHANGE_REFUSED
    if result.status == 400 and str(result.not_saved).startswith('box_link:'):
        return str(result.not_saved)
    return None


def fairplay_save_settings(acting_user, contest, plan, old_key=None):
    """After the editor's save: send changed settings (or, on a key change, the old ones) to FairPlay."""
    from django.contrib.auth.models import User
    from judge.models import Contest
    from judge.utils.fairplay_client import FairPlayClient, FairPlayError, QUICK_READ_TIMEOUT
    if not fairplay_enabled() or not is_fairplay_exam(contest):
        return FairPlaySaveResult()
    user = User.objects.filter(username=acting_user, is_active=True).first() if acting_user else None
    if user is None or not Contest.objects.filter(id=contest.id).exists() or not contest.is_editable_by(user):
        return FairPlaySaveResult(not_saved=_('You are not an author or curator of this assignment.'))
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
        return FairPlaySaveResult(not_saved=e.detail, settings=settings, status=e.status)
    blocked = editor_settings((view or {}).get('settings') if isinstance(view, dict) else None)['websites_blocked']
    if not blocked:
        return FairPlaySaveResult()
    return FairPlaySaveResult(warnings=[_('FairPlay will not open these websites: %s.') % ', '.join(
        '%s (%s)' % (item['site'], item['reason']) if item['reason'] else item['site'] for item in blocked[:10])])
