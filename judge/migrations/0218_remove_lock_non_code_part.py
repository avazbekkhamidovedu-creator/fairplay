from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0217_assignmentdraftfile'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contest',
            name='lock_non_code_part',
        ),
        migrations.RemoveField(
            model_name='contestparticipation',
            name='chosen_part',
        ),
        migrations.RemoveField(
            model_name='contestparticipation',
            name='code_part_completed_at',
        ),
        migrations.RemoveField(
            model_name='contestparticipation',
            name='non_code_part_completed_at',
        ),
    ]
