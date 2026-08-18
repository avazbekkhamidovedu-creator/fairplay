# Merge Organization.instructors with ProblemCodeConfig, and let instructors
# create assignments (private contests).
#
# ContestCreate and the "Add Assignment" tab are gated on
# judge.create_private_contest. The Instructors group previously omitted it, so
# verified teachers could make courses/problems but not assignments.

from django.db import migrations

INSTRUCTORS_GROUP = 'Instructors'
CREATE_ASSIGNMENT_PERMISSION = 'create_private_contest'


def attach_create_assignment_permission(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    group, _created = Group.objects.get_or_create(name=INSTRUCTORS_GROUP)
    perms = Permission.objects.filter(
        content_type__app_label='judge',
        codename=CREATE_ASSIGNMENT_PERMISSION,
    )
    group.permissions.add(*perms)


def detach_create_assignment_permission(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    group = Group.objects.filter(name=INSTRUCTORS_GROUP).first()
    if group is None:
        return
    perms = Permission.objects.filter(
        content_type__app_label='judge',
        codename=CREATE_ASSIGNMENT_PERMISSION,
    )
    group.permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0194_organization_instructors'),
        ('judge', '0200_problemcodeconfig'),
    ]

    operations = [
        migrations.RunPython(
            attach_create_assignment_permission,
            detach_create_assignment_permission,
        ),
    ]
