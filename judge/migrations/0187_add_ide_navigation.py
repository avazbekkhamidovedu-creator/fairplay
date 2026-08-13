from django.db import migrations, models
from django.db.models import F


def add_ide_nav(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    about = NavigationBar.objects.filter(key='about').first()
    user = NavigationBar.objects.filter(key='user').first()
    ide = NavigationBar.objects.filter(key='ide').first()

    # Place IDE immediately after User, which is immediately before About
    # on the current top nav (User | About).
    if user is not None:
        target = user.order + 1
    elif about is not None:
        target = about.order
    else:
        target = 4

    NavigationBar.objects.filter(order__gte=target).exclude(key='ide').update(
        order=F('order') + 1,
    )

    if ide is None:
        max_tree = NavigationBar.objects.aggregate(m=models.Max('tree_id'))['m'] or 0
        NavigationBar.objects.create(
            key='ide',
            label='IDE',
            path='/ide/',
            regex=r'^/ide',
            order=target,
            parent=None,
            level=0,
            lft=1,
            rght=2,
            tree_id=max_tree + 1,
        )
    else:
        ide.order = target
        ide.save(update_fields=['order'])


def remove_ide_nav(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    ide = NavigationBar.objects.filter(key='ide', path='/ide/').first()
    if ide is None:
        return
    order = ide.order
    ide.delete()
    NavigationBar.objects.filter(order__gt=order).update(order=F('order') - 1)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0186_merge_programming_label_and_file_upload'),
    ]

    operations = [
        migrations.RunPython(add_ide_nav, remove_ide_nav),
    ]
