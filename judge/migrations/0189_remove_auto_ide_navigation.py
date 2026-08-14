# Drop IDE from the top nav if a previous revision of 0187 inserted it.
# /ide/ remains; add the tab in Django Admin when desired (same as main).

from django.db import migrations
from django.db.models import F


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
        ('judge', '0188_rebuild_nav_after_ide'),
    ]

    operations = [
        migrations.RunPython(remove_ide_nav, migrations.RunPython.noop),
    ]
