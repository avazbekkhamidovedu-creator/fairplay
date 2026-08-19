# Merge the two develop leaves after courses + assignment (!91) landed in parallel:
#   0201_instructor_create_assignment
#   0202_contest_allow_ai_hints (via 0201_merge_organization_instructors_and_problemcodeconfig)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0201_instructor_create_assignment'),
        ('judge', '0202_contest_allow_ai_hints'),
    ]

    operations = [
    ]
