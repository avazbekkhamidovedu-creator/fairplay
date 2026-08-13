from django.db import migrations
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
        NavigationBar.objects.create(
            key='ide',
            label='IDE',
            path='/ide/',
            regex=r'^/ide',
            order=target,
            parent=None,
        )
    else:
        ide.order = target
        ide.save(update_fields=['order'])

    # Nav is rendered via mptt_tree (tree_id/lft), not bare order. Rebuild so
    # order_insertion_by=['order'] places IDE between User and About.
    from judge.models import NavigationBar as LiveNavigationBar
    LiveNavigationBar.objects.rebuild()


def remove_ide_nav(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    ide = NavigationBar.objects.filter(key='ide', path='/ide/').first()
    if ide is None:
        return
    order = ide.order
    ide.delete()
    NavigationBar.objects.filter(order__gt=order).update(order=F('order') - 1)
    from judge.models import NavigationBar as LiveNavigationBar
    LiveNavigationBar.objects.rebuild()


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0186_merge_programming_label_and_file_upload'),
    ]

    operations = [
        migrations.RunPython(add_ide_nav, remove_ide_nav),
    ]
