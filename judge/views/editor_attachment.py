"""Upload/download for files inserted into the rich-text editor.

Uploads go through Binghan's ``submission_storage`` (OSS primary + Box mirror).
Downloads stream through Django so Markdown can embed a stable site URL; the
object store itself is private (no presigned URLs).
"""
import os
import posixpath

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from judge.models import EditorAttachment
from judge.storage import StorageUnavailableError

MAX_EDITOR_ATTACHMENT_BYTES = 20 * 1024 * 1024
INLINE_IMAGE_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
}


def _can_upload_editor_attachment(user):
    # Any signed-in user may insert images/files in the rich editor (short-answer
    # students included). This is not the Django admin "editor attachment" perm.
    return user.is_authenticated


def is_inline_image_name(filename):
    ext = posixpath.splitext(filename or '')[1].lower()
    return ext in INLINE_IMAGE_TYPES


@login_required
@require_POST
def editor_file_uploader(request):
    if not _can_upload_editor_attachment(request.user):
        return JsonResponse({
            'status': 403,
            'error': _('You do not have permission to upload files.'),
        }, status=403)

    upload = request.FILES.get('markdown-file-upload')
    if upload is None:
        return JsonResponse({'status': 400, 'error': _('No file uploaded.')}, status=400)
    if not upload.size:
        return JsonResponse({'status': 400, 'error': _('The file is empty.')}, status=400)
    if upload.size > MAX_EDITOR_ATTACHMENT_BYTES:
        return JsonResponse({
            'status': 400,
            'error': _('File is too large (maximum %d MB).') % (MAX_EDITOR_ATTACHMENT_BYTES // (1024 * 1024)),
        }, status=400)

    original_name = os.path.basename(upload.name or 'file')[:255] or 'file'
    profile = getattr(request, 'profile', None)
    attachment = EditorAttachment(uploaded_by=profile, original_name=original_name, size=upload.size)
    try:
        attachment.file.save(original_name, upload, save=False)
        attachment.size = upload.size
        attachment.save()
    except StorageUnavailableError:
        return JsonResponse({
            'status': 503,
            'error': _('The file store is temporarily unavailable. Please try again later.'),
        }, status=503)

    url = request.build_absolute_uri(reverse('editor_file_download', args=[attachment.uuid]))
    return JsonResponse({
        'status': 200,
        'name': original_name,
        'link': url,
        'is_image': is_inline_image_name(original_name),
    })


def editor_file_download(request, file_uuid):
    """Stream an editor attachment. UUID URLs are unguessable; no login required
    so public problem statements can render inserted images/links."""
    attachment = get_object_or_404(EditorAttachment, uuid=file_uuid)
    if not attachment.file:
        raise Http404()
    try:
        handle = attachment.file.open('rb')
    except StorageUnavailableError:
        return HttpResponse(
            _('The file store is temporarily unavailable. Please try again later.'),
            status=503, content_type='text/plain',
        )
    except (OSError, ValueError, FileNotFoundError):
        raise Http404()

    ext = posixpath.splitext(attachment.original_name)[1].lower()
    content_type = INLINE_IMAGE_TYPES.get(ext)
    if content_type:
        return FileResponse(
            handle, as_attachment=False, filename=attachment.original_name,
            content_type=content_type,
        )
    # Force attachment + a neutral type: uploaded HTML/SVG must never render inline.
    return FileResponse(
        handle, as_attachment=True, filename=attachment.original_name,
        content_type='application/octet-stream',
    )
