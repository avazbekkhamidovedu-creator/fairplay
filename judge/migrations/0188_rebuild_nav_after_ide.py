# Idempotent fix for environments that already ran 0187 before it called rebuild().
# Nav tabs are rendered with mptt_tree (tree_id), so order-only updates leave IDE last.

from django.db import migrations


def rebuild_nav(apps, schema_editor):
    from judge.models import NavigationBar
    NavigationBar.objects.rebuild()


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0187_add_ide_navigation'),
    ]

    operations = [
        migrations.RunPython(rebuild_nav, migrations.RunPython.noop),
    ]
