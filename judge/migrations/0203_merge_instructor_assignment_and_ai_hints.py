# Merge the two 0201 leaves that both depend on 0194 + 0200:
#   0201_instructor_create_assignment
#   0201_merge_organization_instructors_and_problemcodeconfig -> 0202_contest_allow_ai_hints

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0201_instructor_create_assignment'),
        ('judge', '0202_contest_allow_ai_hints'),
    ]

    operations = []
