# Merge the two develop leaves so later schema changes can migrate:
#   - 0194_organization_instructors (Organization.instructors)
#   - 0200_problemcodeconfig (ProblemCodeConfig)
# Those lines touch disjoint models, so this empty merge only reunites the heads.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0194_organization_instructors'),
        ('judge', '0200_problemcodeconfig'),
    ]

    operations = [
    ]
