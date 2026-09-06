from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0213_contestproblem_display_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='lock_non_code_part',
            field=models.BooleanField(
                default=False,
                help_text='Students choose whether to start with non-programming or programming '
                          'questions. After they finish the first part, they cannot return to it. '
                          'The site IDE is available only while they are working on programming '
                          'problems. If this assignment has only non-programming questions, the '
                          'site IDE stays closed for the whole assignment.',
                verbose_name='lock non-programming part',
            ),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='chosen_part',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Which part the student started: non_code or code.',
                max_length=16,
                verbose_name='chosen assignment part',
            ),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='non_code_part_completed_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the student finished the non-programming part and locked those answers.',
                null=True,
                verbose_name='non-programming part completed',
            ),
        ),
        migrations.AddField(
            model_name='contestparticipation',
            name='code_part_completed_at',
            field=models.DateTimeField(
                blank=True,
                help_text='When the student finished the programming part and locked those answers.',
                null=True,
                verbose_name='programming part completed',
            ),
        ),
    ]
