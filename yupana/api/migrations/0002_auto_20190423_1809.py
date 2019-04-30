# Generated by Django 2.2 on 2019-04-23 18:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial_report'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReportSlice',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_platform_id', models.CharField(max_length=50, null=True)),
                ('report_slice_id', models.CharField(max_length=50, null=True)),
                ('rh_account', models.TextField(null=True)),
                ('upload_ack_status', models.TextField(null=True)),
                ('upload_srv_kafka_msg', models.TextField(null=True)),
                ('report_json', models.TextField(null=True)),
                ('git_commit', models.TextField(null=True)),
                ('state', models.CharField(choices=[('PENDING', 'pending'), ('NEW', 'new'), ('STARTED', 'started'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28)),
                ('retry_type', models.CharField(choices=[('TIME', 'time'), ('GIT_COMMIT', 'git commit')], default='time', max_length=10)),
                ('state_info', models.TextField(null=True)),
                ('retry_count', models.PositiveSmallIntegerField(null=True)),
                ('last_update_time', models.DateTimeField(null=True)),
                ('failed_hosts', models.TextField(null=True)),
                ('candidate_hosts', models.TextField(null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ReportSliceArchive',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_platform_id', models.CharField(max_length=50, null=True)),
                ('report_slice_id', models.CharField(max_length=50, null=True)),
                ('rh_account', models.TextField(null=True)),
                ('upload_ack_status', models.TextField(null=True)),
                ('upload_srv_kafka_msg', models.TextField(null=True)),
                ('report_json', models.TextField(null=True)),
                ('git_commit', models.TextField(null=True)),
                ('state', models.CharField(choices=[('PENDING', 'pending'), ('NEW', 'new'), ('STARTED', 'started'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28)),
                ('retry_type', models.CharField(choices=[('TIME', 'time'), ('GIT_COMMIT', 'git commit')], default='time', max_length=10)),
                ('state_info', models.TextField(null=True)),
                ('retry_count', models.PositiveSmallIntegerField(null=True)),
                ('last_update_time', models.DateTimeField(null=True)),
                ('failed_hosts', models.TextField(null=True)),
                ('candidate_hosts', models.TextField(null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RemoveField(
            model_name='report',
            name='candidate_hosts',
        ),
        migrations.RemoveField(
            model_name='report',
            name='failed_hosts',
        ),
        migrations.RemoveField(
            model_name='report',
            name='report_json',
        ),
        migrations.RemoveField(
            model_name='reportarchive',
            name='candidate_hosts',
        ),
        migrations.RemoveField(
            model_name='reportarchive',
            name='failed_hosts',
        ),
        migrations.RemoveField(
            model_name='reportarchive',
            name='report_json',
        ),
        migrations.AddField(
            model_name='report',
            name='qpc_server_id',
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='report',
            name='qpc_server_version',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='report',
            name='report_slices',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='report',
            name='report_version',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='reportarchive',
            name='qpc_server_id',
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='reportarchive',
            name='qpc_server_version',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='reportarchive',
            name='report_slices',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='reportarchive',
            name='report_version',
            field=models.TextField(null=True),
        ),
        migrations.AlterField(
            model_name='report',
            name='state',
            field=models.CharField(choices=[('NEW', 'new'), ('STARTED', 'started'), ('DOWNLOADED', 'downloaded'), ('FAILED_DOWNLOAD', 'failed to download'), ('VALIDATED', 'validated'), ('FAILED_VALIDATION', 'failed validation'), ('VALIDATION_REPORTED', 'validation reported'), ('FAILED_VALIDATION_REPORTING', 'failed to report validation')], default='new', max_length=28),
        ),
        migrations.AlterField(
            model_name='reportarchive',
            name='state',
            field=models.CharField(choices=[('NEW', 'new'), ('STARTED', 'started'), ('DOWNLOADED', 'downloaded'), ('FAILED_DOWNLOAD', 'failed to download'), ('VALIDATED', 'validated'), ('FAILED_VALIDATION', 'failed validation'), ('VALIDATION_REPORTED', 'validation reported'), ('FAILED_VALIDATION_REPORTING', 'failed to report validation')], default='new', max_length=28),
        ),
    ]
