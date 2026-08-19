"""Clone a class offering: materials (course content) and assignment problem lists."""
from __future__ import annotations

import logging
import re

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.translation import gettext as _
from reversion import revisions

from judge.models import Class, ClassMaterial, Contest, ContestProblem

logger = logging.getLogger(__name__)


def unique_contest_key(base):
    cleaned = re.sub(r'[^a-z0-9]', '', (base or '').lower())[:16] or 'c'
    candidate = cleaned
    n = 2
    while Contest.objects.filter(key=candidate).exists():
        suffix = str(n)
        candidate = ('%s%s' % (cleaned[: 20 - len(suffix)], suffix))[:20]
        n += 1
    return candidate


def unique_class_name(name):
    candidate = (name or _('Copy'))[:128]
    n = 2
    while Class.objects.filter(name=candidate).exists():
        suffix = ' (%d)' % n
        candidate = ('%s%s' % (name[: 128 - len(suffix)], suffix))[:128]
        n += 1
    return candidate


def clone_contest(source, new_key, author, *, target_class=None, copy_private_contestants=True):
    """Copy an assignment/exam's settings and problem list. Does not copy submissions."""
    tags = list(source.tags.all())
    organizations = list(source.organizations.all())
    join_organizations = list(source.join_organizations.all())
    private_contestants = list(source.private_contestants.all()) if copy_private_contestants else []
    view_contest_scoreboard = list(source.view_contest_scoreboard.all())
    authors = list(source.authors.all())
    curators = list(source.curators.all())
    testers = list(source.testers.all())
    contest_problems = list(source.contest_problems.all())
    orig_classes = list(source.classes.all())
    old_key = source.key

    clone = Contest.objects.get(pk=source.pk)
    clone.pk = None
    clone.is_visible = False
    clone.user_count = 0
    clone.locked_after = None
    clone.results_finalized = False
    clone.key = new_key
    if hasattr(clone, 'ai_grading_scheduled_task_id'):
        clone.ai_grading_scheduled_task_id = ''
        clone.ai_grading_batch_started_at = None
        clone.ai_grading_batch_finished_at = None

    with revisions.create_revision(atomic=True):
        clone.save()
        clone.tags.set(tags)
        clone.organizations.set(organizations)
        clone.join_organizations.set(join_organizations)
        clone.private_contestants.set(private_contestants)
        clone.view_contest_scoreboard.set(view_contest_scoreboard)
        clone.authors.set(authors)
        clone.authors.add(author)
        clone.curators.set(curators)
        clone.testers.set(testers)
        if target_class is not None:
            clone.classes.set([target_class])
        else:
            clone.classes.set(orig_classes)
        for problem in contest_problems:
            problem.contest = clone
            problem.pk = None
        ContestProblem.objects.bulk_create(contest_problems)
        revisions.set_user(author.user if hasattr(author, 'user') else author)
        revisions.set_comment(_('Cloned contest from %s') % old_key)
    return clone


def copy_class_materials(source_class, dest_class, uploader):
    """Deep-copy folders and files. Link-only rows (no file) keep the same name."""
    id_map = {None: None}
    copied = 0
    rows = list(ClassMaterial.objects.filter(class_obj=source_class).order_by('id'))
    pending = list(rows)
    safety = 0
    while pending and safety < len(rows) + 5:
        safety += 1
        leftover = []
        for item in pending:
            parent_id = item.parent_id
            if parent_id is not None and parent_id not in id_map:
                leftover.append(item)
                continue
            new = ClassMaterial(
                class_obj=dest_class,
                parent_id=id_map[parent_id],
                name=item.name,
                is_folder=item.is_folder,
                uploaded_by=uploader,
            )
            new.save()
            if not item.is_folder and item.file:
                try:
                    item.file.open('rb')
                    data = item.file.read()
                except Exception:
                    logger.exception('Could not read class material %s', item.id)
                    data = None
                finally:
                    try:
                        item.file.close()
                    except Exception:
                        pass
                if data is not None:
                    new.file.save(item.name, ContentFile(data), save=True)
            id_map[item.id] = new.id
            copied += 1
        if len(leftover) == len(pending):
            break
        pending = leftover
    return copied


@transaction.atomic
def clone_class(source, *, name, profile, copy_materials=True, copy_assignments=True, **field_overrides):
    """Create a new class offering with optional materials and cloned assignments.

    Students, join requests, and submissions are never copied.
    """
    from judge.views.organization import unique_class_slug

    dest = Class(
        organization=source.organization,
        name=unique_class_name(name),
        description=source.description,
        academic_year=field_overrides.get('academic_year', source.academic_year),
        term_session=field_overrides.get('term_session', source.term_session),
        credits=source.credits,
        location=source.location,
        start_date=field_overrides.get('start_date'),
        end_date=field_overrides.get('end_date'),
        is_active=True,
        access_code=None,
        instructor_names=field_overrides.get('instructor_names', source.instructor_names),
        ta_names=source.ta_names,
    )
    dest.slug = unique_class_slug(source.organization, dest.name)
    dest.save()
    dest.admins.set(source.admins.all())
    dest.admins.add(profile)

    materials = 0
    if copy_materials:
        materials = copy_class_materials(source, dest, profile)

    assignments = 0
    if copy_assignments:
        for contest in Contest.objects.filter(classes=source).order_by('id'):
            clone_contest(
                contest,
                unique_contest_key(contest.key),
                profile,
                target_class=dest,
                copy_private_contestants=False,
            )
            assignments += 1
    return dest, materials, assignments
