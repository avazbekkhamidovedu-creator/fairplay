"""The assignment editor's reply after a save."""
import logging

from judge.utils.fairplay_editor import fairplay_save_settings

__all__ = ['fairplay_editor_saved']

logger = logging.getLogger('judge.fairplay')


def fairplay_editor_saved(request, contest, plan, old_key=None):
    """Sends changed settings to FairPlay after an editor save; the editor shows nothing about it."""
    result = fairplay_save_settings(request.user.username, contest, plan, old_key=old_key)
    if result.not_saved is not None:
        logger.warning('FairPlay settings of %s were not sent: %s', contest.key, result.not_saved)
    return None
