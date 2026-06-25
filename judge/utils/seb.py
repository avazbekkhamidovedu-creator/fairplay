import hashlib
import hmac
from urllib.parse import urlparse, urlunparse

from django.urls import reverse

# WSGI normalizes header names: X-SafeExamBrowser-RequestHash -> HTTP_X_SAFEEXAMBROWSER_REQUESTHASH
BEK_HEADER = 'HTTP_X_SAFEEXAMBROWSER_REQUESTHASH'
CFG_HEADER = 'HTTP_X_SAFEEXAMBROWSER_CONFIGKEYHASH'

SEB_INSTALL_URL = 'https://safeexambrowser.org/download_en.html'


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
    if contest.seb_config_file:
        download_url = request.build_absolute_uri(reverse('contest_seb_config', args=(contest.key,)))
        parsed = urlparse(download_url)
        sebs_scheme = 'sebs' if parsed.scheme == 'https' else 'seb'
        sebs_url = urlunparse((sebs_scheme, parsed.netloc, parsed.path,
                               parsed.params, parsed.query, parsed.fragment))
    return {
        'download_url': download_url,
        'sebs_url': sebs_url,
        'quit_url': contest.seb_quit_url or '',
        'install_url': SEB_INSTALL_URL,
    }
