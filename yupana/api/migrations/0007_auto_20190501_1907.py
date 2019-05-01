# Generated by Django 2.2 on 2019-05-01 19:07

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0006_auto_20190430_1822'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reportslice',
            name='state',
            field=models.CharField(choices=[('PENDING', 'pending'), ('NEW', 'new'), ('RETRY_VALIDATION', 'retry_validation'), ('FAILED_VALIDATION', 'failed_validation'), ('VALIDATED', 'validated'), ('STARTED', 'started'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28),
        ),
        migrations.AlterField(
            model_name='reportslicearchive',
            name='state',
            field=models.CharField(choices=[('PENDING', 'pending'), ('NEW', 'new'), ('RETRY_VALIDATION', 'retry_validation'), ('FAILED_VALIDATION', 'failed_validation'), ('VALIDATED', 'validated'), ('STARTED', 'started'), ('HOSTS_UPLOADED', 'hosts uploaded'), ('FAILED_HOSTS_UPLOAD', 'failed to upload hosts')], default='new', max_length=28),
        ),
    ]
