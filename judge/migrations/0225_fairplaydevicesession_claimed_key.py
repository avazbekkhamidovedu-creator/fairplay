from django.db import migrations, models


def backfill(apps, schema_editor):
    Session = apps.get_model('judge', 'FairPlayDeviceSession')
    for session in Session.objects.filter(claimed_key='').select_related('contest').iterator():
        Session.objects.filter(pk=session.pk).update(claimed_key=session.contest.key)


class Migration(migrations.Migration):
    dependencies = [('judge', '0224_contest_had_fairplay')]
    operations = [
        migrations.AddField(model_name='fairplaydevicesession', name='claimed_key',
                            field=models.CharField(blank=True, default='', max_length=20)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
