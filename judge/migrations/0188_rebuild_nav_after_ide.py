# Kept for migration graph compatibility with earlier branch history.
# IDE nav removal lives in 0189_remove_auto_ide_navigation.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0187_add_ide_navigation'),
    ]

    operations = []
