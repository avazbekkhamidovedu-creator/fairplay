from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import exists, select

from ...models import Exam, SchoolClass, User
from .client import OjError
from .models import ClassLogin, OjState

BINDING = 'installation_binding'
ERROR = 'installation_error'


def canonical_url(value):
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise OjError('Invalid DKU OJ installation URL', 409)
    host = parts.hostname.lower().rstrip('.')
    if ':' in host:
        host = f'[{host}]'
    port = parts.port
    if port and port != (443 if parts.scheme == 'https' else 80):
        host += f':{port}'
    return urlunsplit((parts.scheme, host, parts.path.rstrip('/'), '', ''))


async def require_binding(session, cfg):
    import json
    row = await session.get(OjState, BINDING)
    error = await session.get(OjState, ERROR)
    try:
        bound = json.loads(row.value) if row else {}
        current = canonical_url(cfg.url)
        aliases = {canonical_url(u) for u in cfg.verified_url_aliases}
        valid = (bound.get('installation_id') == cfg.installation_id and cfg.installation_id
                 and (bound.get('url') == current or current in aliases))
    except (ValueError, TypeError, OjError):
        valid = False
    if not valid or (error and error.value):
        raise HTTPException(503, 'DKU OJ installation binding needs operator verification')


async def verify_installation(sessionmaker, client, cfg):
    import json
    from .sync import set_state
    try:
        if not cfg.installation_id:
            raise OjError('DKUOJ_INSTALLATION_ID is not configured', 409)
        identity = await client.installation()
        async with sessionmaker() as session:
            row = await session.get(OjState, BINDING)
            current = canonical_url(cfg.url)
            if row:
                bound = json.loads(row.value or '{}')
                if bound.get('installation_id') != identity['installation_id']:
                    raise OjError('Different DKU OJ installation: use a separate database or reviewed migration', 409)
                if bound.get('url') != current and current not in {canonical_url(u) for u in cfg.verified_url_aliases}:
                    raise OjError('DKU OJ URL changed without an explicitly verified alias', 409)
            else:
                legacy = any([await session.scalar(select(exists().where(Exam.source == 'dkuoj'))),
                              await session.scalar(select(exists().where(User.netid.is_not(None)))),
                              await session.scalar(select(exists().where(SchoolClass.external_ref.startswith('dkuoj:')))),
                              await session.scalar(select(exists().where(ClassLogin.id.is_not(None))))])
                if legacy and cfg.confirm_existing_installation_id != cfg.installation_id:
                    raise OjError('Existing DKU OJ data needs explicit DKUOJ_CONFIRM_EXISTING_INSTALLATION_ID', 409)
                await set_state(session, BINDING, json.dumps({'installation_id': cfg.installation_id, 'url': current}))
            await set_state(session, ERROR, None)
    except (OjError, ValueError, TypeError) as error:
        message = error.message if isinstance(error, OjError) else 'Invalid stored DKU OJ installation binding'
        async with sessionmaker() as session:
            await set_state(session, ERROR, message)
        raise OjError(message, 409) from None
