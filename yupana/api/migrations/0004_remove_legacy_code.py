# Generated by Django 2.2.6 on 2019-11-12 13:26

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0003_source_metadata_report_slice'),
    ]

    operations = [
        migrations.DeleteModel(
            name='InventoryUploadError',
        ),
        migrations.RemoveField(
            model_name='legacyreportslice',
            name='report',
        ),
        migrations.RemoveField(
            model_name='legacyreportslicearchive',
            name='report',
        ),
        migrations.DeleteModel(
            name='LegacyReport',
        ),
        migrations.DeleteModel(
            name='LegacyReportArchive',
        ),
        migrations.DeleteModel(
            name='LegacyReportSlice',
        ),
        migrations.DeleteModel(
            name='LegacyReportSliceArchive',
        ),
    ]
