from django.db import migrations, models


def backfill(apps, schema_editor):
    Contest = apps.get_model('judge', 'Contest')
    Contest.objects.filter(tags__name='fairplay').update(had_fairplay=True)


class Migration(migrations.Migration):
    dependencies = [('judge', '0223_contestkeyalias')]
    operations = [
        migrations.AddField(model_name='contest', name='had_fairplay',
                            field=models.BooleanField(default=False, editable=False,
                                                      help_text='Set once the FairPlay lock has been on; keeps hand-in delivery working after the lock is switched off.')),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
