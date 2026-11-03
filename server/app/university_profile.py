"""Validation primitives for a future university launch adapter, NOT an auth endpoint.

No routes import this module yet. A caller must authenticate the university adapter,
verify the signed launch, check enrollment and atomically consume its nonce first.
These checks do not attest that a Mac is locked or that an LMS protects questions.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit
import re

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CANVAS_ITEM = re.compile(r"/courses/[0-9]{1,20}/(?:quizzes|assignments)/[0-9]{1,20}\Z")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")


def exact_https_origin(value: str) -> str:
    """A deliberately narrow origin syntax shared easily with a native client."""
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("origin must be a short HTTPS origin")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or any(c in value for c in "\\%@;?#"):
        raise ValueError("origin contains ambiguous characters")
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError:
        raise ValueError("invalid origin") from None
    if (p.scheme != "https" or p.path or p.query or p.fragment or p.username or p.password
            or port not in (None, 443) or not _HOST.fullmatch(p.hostname or "")):
        raise ValueError("expected an HTTPS DNS origin without path, credentials or custom port")
    return "https://" + p.hostname


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid opaque identifier")
    return value


@dataclass(frozen=True)
class UniversityProfile:
    university_id: str
    portal_origin: str
    fairplay_origin: str
    browse_origins: tuple[str, ...]
    adapter_id: str
    revision: int

    def __post_init__(self):
        _identifier(self.university_id)
        _identifier(self.adapter_id)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("revision must be a positive integer")
        for name in ("portal_origin", "fairplay_origin"):
            object.__setattr__(self, name, exact_https_origin(getattr(self, name)))
        if not isinstance(self.browse_origins, tuple) or not 1 <= len(self.browse_origins) <= 32:
            raise ValueError("browse_origins must contain 1 to 32 exact origins")
        normalized = tuple(exact_https_origin(o) for o in self.browse_origins)
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate browse origin")
        if self.fairplay_origin in normalized:
            raise ValueError("FairPlay must not be a university browsing origin")
        if self.portal_origin not in normalized:
            raise ValueError("portal must be an approved browse origin")
        object.__setattr__(self, "browse_origins", normalized)


@dataclass(frozen=True)
class LaunchContext:
    """Authenticated server context; never take these values from browser query params."""
    university_id: str
    adapter_id: str
    subject_id: str
    exam_id: str
    attempt_id: str
    app_session_id: str
    device_id: str
    profile_revision: int


def validate_verified_launch(claims: dict, context: LaunchContext,
                             profile: UniversityProfile, now: int) -> None:
    """Check semantic binding AFTER signature/authentication and BEFORE atomic use.

    The signature layer must enforce algorithm/key/issuer allowlists and reject
    duplicate JSON members. The database layer must consume jti atomically with
    attempt admission. Returning does NOT itself authorize question access.
    """
    if type(claims) is not dict or type(now) is not int:
        raise ValueError("invalid claims or clock")
    if (context.university_id != profile.university_id or context.adapter_id != profile.adapter_id
            or context.profile_revision != profile.revision):
        raise ValueError("profile mismatch")
    expected = {"university_id": context.university_id, "adapter_id": context.adapter_id,
                "sub": context.subject_id, "exam_id": context.exam_id,
                "attempt_id": context.attempt_id, "app_session_id": context.app_session_id,
                "device_id": context.device_id}
    for key, value in expected.items():
        _identifier(value)
        if claims.get(key) != value:
            raise ValueError("launch binding mismatch: " + key)
    if (claims.get("aud") != profile.fairplay_origin or claims.get("purpose") != "fairplay.exam.launch.v1"
            or type(claims.get("profile_revision")) is not int
            or claims["profile_revision"] != profile.revision):
        raise ValueError("launch audience, purpose or revision mismatch")
    _identifier(claims.get("jti"))
    iat, exp = claims.get("iat"), claims.get("exp")
    if type(iat) is not int or type(exp) is not int or not (iat <= now < exp <= iat + 120):
        raise ValueError("launch expired, future-dated or too long-lived")
    # No caller-controlled question URL, policy or allowed-domain override is supported.
    if any(k in claims for k in ("url", "return_url", "allowed_domains", "lock_policy", "question_url")):
        raise ValueError("launch must reference server-owned exam configuration")


@dataclass(frozen=True)
class ProtectedLaunch:
    """Server-owned mapping, not a general URL detector or proof of an LMS gate."""
    launch_url: str
    exam_id: str


def validate_protected_launches(profile: UniversityProfile,
                               launches: tuple[ProtectedLaunch, ...]) -> dict[str, str]:
    """Return exact canonical launch URL -> internal exam ID. No redirect URLs."""
    if not isinstance(launches, tuple) or not 1 <= len(launches) <= 1000:
        raise ValueError("expected a bounded nonempty launch mapping")
    result = {}
    for entry in launches:
        _identifier(entry.exam_id)
        raw = entry.launch_url
        if (not isinstance(raw, str) or len(raw) > 2000
                or any(ord(c) <= 32 or ord(c) >= 127 for c in raw)
                or any(c in raw for c in "\\%@;?#")):
            raise ValueError("ambiguous protected launch URL")
        try:
            p = urlsplit(raw)
            origin = exact_https_origin(p.scheme + "://" + p.netloc)
        except ValueError:
            raise ValueError("invalid protected launch URL") from None
        if origin != profile.portal_origin or not _CANVAS_ITEM.fullmatch(p.path):
            raise ValueError("launch must be a canonical Canvas quiz or assignment on the university portal")
        canonical = origin + p.path
        if canonical in result:
            raise ValueError("duplicate protected launch URL")
        result[canonical] = entry.exam_id
    return result
