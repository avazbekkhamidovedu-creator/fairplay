# Intentionally a no-op.
# IDE stays available at /ide/ (local Monaco); navbar entry is Admin-managed like main,
# not auto-inserted by migration.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0186_merge_programming_label_and_file_upload'),
    ]

    operations = []
