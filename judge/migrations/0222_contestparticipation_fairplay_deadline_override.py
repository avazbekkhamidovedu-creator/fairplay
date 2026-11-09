from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('judge', '0221_alter_fairplaydevicesession_state')]
    operations = [migrations.AddField(model_name='contestparticipation', name='fairplay_deadline_override',
                                     field=models.DateTimeField(null=True, blank=True, editable=False)),
                  migrations.AddField(model_name="fairplaydevicesession", name="reopen_until",
                                      field=models.DateTimeField(null=True, blank=True))]
