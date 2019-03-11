# Generated by Django 2.1.7 on 2019-03-11 15:39

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_platform_id', models.CharField(max_length=50, null=True)),
                ('rh_account', models.TextField(null=True)),
                ('upload_ack_status', models.TextField(null=True)),
                ('upload_srv_kafka_msg', models.TextField(null=True)),
                ('report_json', models.TextField(null=True)),
                ('git_commit', models.TextField(null=True)),
                ('state', models.CharField(choices=[('NEW', 'new'), ('STARTED', 'started'), ('DOWNLOADED', 'downloaded'), ('FAILED_DOWNLOAD', 'failed to download'), ('VALIDATED', 'validated'), ('FAILED_VALIDATION', 'failed validation'), ('VALIDATION_REPORTED', 'validation reported'), ('FAILED_VALIDATION_REPORTING', 'failed to report validation'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28)),
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
            name='ReportArchive',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_platform_id', models.CharField(max_length=50, null=True)),
                ('rh_account', models.TextField(null=True)),
                ('upload_ack_status', models.TextField(null=True)),
                ('upload_srv_kafka_msg', models.TextField(null=True)),
                ('report_json', models.TextField(null=True)),
                ('git_commit', models.TextField(null=True)),
                ('state', models.CharField(choices=[('NEW', 'new'), ('STARTED', 'started'), ('DOWNLOADED', 'downloaded'), ('FAILED_DOWNLOAD', 'failed to download'), ('VALIDATED', 'validated'), ('FAILED_VALIDATION', 'failed validation'), ('VALIDATION_REPORTED', 'validation reported'), ('FAILED_VALIDATION_REPORTING', 'failed to report validation'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28)),
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
    ]
