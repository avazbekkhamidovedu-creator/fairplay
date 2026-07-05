# Fill-in-the-blank and short-answer question models: per-problem config and blank answers.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0166_exam_contest_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProblemFillBlankConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('grading_mode', models.CharField(choices=[('exact', 'Exact match'), ('ai', 'AI grading')], default='exact', help_text='Exact match compares each blank to its accepted answers; AI grading asks the configured model to score the response against the rubric.', max_length=8, verbose_name='grading mode')),
                ('case_sensitive', models.BooleanField(default=False, help_text='When exact-matching, require the same letter case.', verbose_name='case sensitive')),
                ('ai_rubric', models.TextField(blank=True, help_text='Grading guidance sent to the model (never shown to students).', verbose_name='AI rubric')),
                ('problem', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='fill_blank_config', to='judge.problem', verbose_name='problem')),
            ],
            options={
                'verbose_name': 'fill-in-the-blank configuration',
                'verbose_name_plural': 'fill-in-the-blank configurations',
            },
        ),
        migrations.CreateModel(
            name='ProblemBlankAnswer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField(verbose_name='order')),
                ('answers', models.TextField(help_text='Alternatives separated by "|".', verbose_name='accepted answers')),
                ('problem', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blank_answers', to='judge.problem', verbose_name='problem')),
            ],
            options={
                'verbose_name': 'fill-in-the-blank answer',
                'verbose_name_plural': 'fill-in-the-blank answers',
                'ordering': ('order',),
                'unique_together': {('problem', 'order')},
            },
        ),
        migrations.CreateModel(
            name='ProblemShortAnswerConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reference_answer', models.TextField(blank=True, help_text='Model answer the response is compared against (never shown to students).', verbose_name='reference answer')),
                ('ai_rubric', models.TextField(blank=True, help_text='Grading guidance sent to the model (never shown to students).', verbose_name='AI rubric')),
                ('problem', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='short_answer_config', to='judge.problem', verbose_name='problem')),
            ],
            options={
                'verbose_name': 'short-answer configuration',
                'verbose_name_plural': 'short-answer configurations',
            },
        ),
    ]
