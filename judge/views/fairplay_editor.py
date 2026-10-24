"""The assignment editor's FairPlay messages and reply after a save."""
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _

from judge.utils.fairplay_editor import fairplay_save_settings, fairplay_shared_warning, stash_unsaved_settings

__all__ = ['fairplay_editor_saved']


def fairplay_editor_saved(request, contest, plan, old_key=None):
    """After an editor save; returns the editor's reply when FairPlay did not take the settings."""
    warning = fairplay_shared_warning(contest)
    if warning:
        messages.warning(request, warning)
    result = fairplay_save_settings(request.user.username, contest, plan, old_key=old_key)
    for text in result.warnings:
        messages.warning(request, text)
    if result.not_saved is None:
        return None
    detail = _('The assignment is saved, but its FairPlay settings are not: %s. They are kept on the '
               "assignment's FairPlay tab: save them there once FairPlay is reachable.") % result.not_saved
    stash_unsaved_settings(request, contest.key, result.settings or {}, result.not_saved)
    messages.warning(request, detail)
    return JsonResponse({'ok': True, 'action': 'save', 'key': contest.key, 'fairplay_warning': detail,
                         'redirect': reverse('contest_fairplay', args=[contest.key])})
