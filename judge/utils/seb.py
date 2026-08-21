import hashlib
import hmac
import re
from urllib.parse import urlparse, urlunparse

from django.urls import reverse

# WSGI normalizes header names: X-SafeExamBrowser-RequestHash -> HTTP_X_SAFEEXAMBROWSER_REQUESTHASH
BEK_HEADER = 'HTTP_X_SAFEEXAMBROWSER_REQUESTHASH'
CFG_HEADER = 'HTTP_X_SAFEEXAMBROWSER_CONFIGKEYHASH'

SEB_INSTALL_URL = 'https://safeexambrowser.org/download_en.html'

# Both Browser Exam Keys and Config Keys are SHA-256 hashes in lowercase hex.
SEB_KEY_RE = re.compile('[0-9a-f]{64}')


def parse_seb_keys(text):
    """Split a key textarea into hashes: one per line, trailing comments dropped."""
    out = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        token = line.split()[0].lower()
        if token:
            out.append(token)
    return out


def first_invalid_seb_key(text):
    """Return the first parsed key that is not a 64-char hex hash, else ``None``."""
    for key in parse_seb_keys(text):
        if not SEB_KEY_RE.fullmatch(key):
            return key
    return None


def _sha256_hex(absolute_url, key):
    return hashlib.sha256((absolute_url + key).encode('utf-8')).hexdigest()


def request_matches_seb(request, allowed_bek, allowed_cfg):
    if not allowed_bek and not allowed_cfg:
        return False
    # SEB hashes the absolute URL including the query string; only the fragment
    # is stripped, and fragments never reach the server anyway.
    url = request.build_absolute_uri()
    for hdr, keys in ((BEK_HEADER, allowed_bek), (CFG_HEADER, allowed_cfg)):
        received = request.META.get(hdr)
        if not received:
            continue
        received = received.strip().lower()
        for k in keys:
            if hmac.compare_digest(received, _sha256_hex(url, k)):
                return True
    return False


def seb_launch_context(request, contest):
    download_url = None
    sebs_url = None
    if contest.seb_effective_config_file:
        download_url = request.build_absolute_uri(reverse('contest_seb_config', args=(contest.key,)))
        parsed = urlparse(download_url)
        sebs_scheme = 'sebs' if parsed.scheme == 'https' else 'seb'
        sebs_url = urlunparse((sebs_scheme, parsed.netloc, parsed.path,
                               parsed.params, parsed.query, parsed.fragment))
    return {
        'download_url': download_url,
        'sebs_url': sebs_url,
        'quit_url': contest.seb_effective_quit_url,
        'install_url': SEB_INSTALL_URL,
    }
