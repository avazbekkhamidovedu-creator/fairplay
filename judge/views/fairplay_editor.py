"""The assignment editor's reply after a save."""
import logging
import re

from judge.utils.fairplay_editor import fairplay_save_settings

__all__ = ['fairplay_editor_saved']

logger = logging.getLogger('judge.fairplay')


_SHARED_LINK = re.compile(r"https?://[^\s'\"<>]*box\.com/s/[^\s'\"<>]*", re.IGNORECASE)


def _redacted(text):
    """A Duke Box shared link works for whoever holds it: it never goes to the logs."""
    return _SHARED_LINK.sub('[shared link]', str(text))


def fairplay_editor_saved(request, contest, plan, old_key=None):
    """Sends changed settings to FairPlay after an editor save. A refusal never fails the save: it is only logged."""
    result = fairplay_save_settings(request.user.username, contest, plan, old_key=old_key)
    if result.not_saved is not None:
        logger.warning('FairPlay settings of %s were not sent (status %s): %s', contest.key, result.status,
                       _redacted(result.not_saved))
    return None
