"""The assignment editor's reply after a save."""
import logging

from django.http import JsonResponse
from django.urls import reverse

from judge.utils.fairplay_editor import fairplay_box_error, fairplay_save_settings

__all__ = ['fairplay_editor_saved']

logger = logging.getLogger('judge.fairplay')


def fairplay_editor_saved(request, contest, plan, old_key=None):
    """Sends changed settings to FairPlay after an editor save; only a refused Duke Box link is shown."""
    result = fairplay_save_settings(request.user.username, contest, plan, old_key=old_key)
    if result.not_saved is None:
        return None
    message = fairplay_box_error(result)
    detail = 'the Duke Box folder' if result.settings and 'box_link' in result.settings else result.not_saved
    logger.warning('FairPlay settings of %s were not sent (status %s): %s', contest.key, result.status, detail)
    if message is None:
        return None
    return JsonResponse({'ok': False, 'saved': True, 'field': 'fairplay_box_link', 'error': message,
                         'key': contest.key, 'redirect': reverse('contest_edit', args=[contest.key])})
