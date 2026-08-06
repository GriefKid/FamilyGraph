"""Import the pre-multitenancy FamilyGraph JSON backup into the current schema."""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from main.models import (
    AlertAction,
    Event,
    Group,
    Information,
    JournalEntry,
    JournalImage,
    Node,
    Relationship,
)


class Command(BaseCommand):
    help = 'Import backup_data.json and assign its legacy data to one user.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            default='backup_data.json',
            help='Path to the legacy JSON backup (default: backup_data.json).',
        )
        parser.add_argument(
            '--username',
            default='',
            help='Username for the migrated owner (default: the legacy username).',
        )

    def handle(self, *args, **options):
        source = Path(options['source'])
        if not source.exists():
            raise CommandError(f'Backup file not found: {source}')

        try:
            rows = json.loads(source.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError(f'Could not read backup: {error}') from error

        by_model = {}
        for row in rows:
            by_model.setdefault(row.get('model'), []).append(row)

        legacy_users = by_model.get('auth.user', [])
        if not legacy_users:
            raise CommandError('The backup does not include its legacy auth.user record.')
        legacy_user = legacy_users[0]['fields']
        username = options['username'] or legacy_user['username']
        User = get_user_model()

        if any(
            model.objects.exists()
            for model in (Group, Node, Relationship, Information, Event, JournalEntry)
        ):
            raise CommandError('Target database is not empty; import into a fresh database only.')

        with transaction.atomic():
            user, _created = User.objects.update_or_create(
                username=username,
                defaults={
                    'password': legacy_user['password'],
                    'last_login': legacy_user.get('last_login'),
                    'is_superuser': legacy_user.get('is_superuser', False),
                    'first_name': legacy_user.get('first_name', ''),
                    'last_name': legacy_user.get('last_name', ''),
                    'email': legacy_user.get('email', ''),
                    'is_staff': legacy_user.get('is_staff', False),
                    'is_active': legacy_user.get('is_active', True),
                    'date_joined': legacy_user.get('date_joined'),
                },
            )

            for row in by_model.get('main.group', []):
                fields = row['fields']
                Group.objects.create(
                    pk=row['pk'],
                    name=fields['name'],
                    color=fields.get('color', ''),
                    owner=user,
                )

            node_groups = {}
            for row in by_model.get('main.node', []):
                fields = row['fields']
                node_groups[row['pk']] = fields.get('groups', [])
                Node.objects.create(
                    pk=row['pk'],
                    username=fields['username'],
                    first_name=fields.get('first_name', ''),
                    last_name=fields.get('last_name', ''),
                    nickname=fields.get('nickname', ''),
                    picture=fields.get('picture') or None,
                    name=fields.get('name', ''),
                    birth_day=fields.get('birth_day'),
                    career=fields.get('career', ''),
                    phone_number=fields.get('phone_number', ''),
                    group=fields.get('group', ''),
                    owner=user,
                )
            for node_id, group_ids in node_groups.items():
                Node.objects.get(pk=node_id).groups.set(group_ids)

            for row in by_model.get('main.relationship', []):
                fields = row['fields']
                Relationship.objects.create(
                    pk=row['pk'],
                    rel=fields.get('rel'),
                    source_id=fields['source'],
                    target_id=fields['target'],
                    strength=fields.get('strength', 3),
                    status=fields.get('status', 'active'),
                    met_at=fields.get('met_at'),
                    owner=user,
                )

            for row in by_model.get('main.information', []):
                fields = row['fields']
                Information.objects.create(
                    pk=row['pk'],
                    node_id=fields['node'],
                    visibility=fields.get('visibility', 'private'),
                    data=fields.get('data'),
                )

            event_participants = {}
            for row in by_model.get('main.event', []):
                fields = row['fields']
                event_participants[row['pk']] = fields.get('participants', [])
                Event.objects.create(
                    pk=row['pk'],
                    title=fields['title'],
                    date=fields['date'],
                    description=fields.get('description', ''),
                    owner=user,
                )
            for event_id, node_ids in event_participants.items():
                Event.objects.get(pk=event_id).participants.set(node_ids)

            journal_mentions = {}
            for row in by_model.get('main.journalentry', []):
                fields = row['fields']
                journal_mentions[row['pk']] = fields.get('mentioned_nodes', [])
                entry = JournalEntry.objects.create(
                    pk=row['pk'],
                    text=fields['text'],
                    entry_date=fields.get('entry_date'),
                    tags=fields.get('tags', []),
                    mood=fields.get('mood', ''),
                    ai_analyzed=fields.get('ai_analyzed', False),
                    owner=user,
                )
                if fields.get('created_at'):
                    JournalEntry.objects.filter(pk=entry.pk).update(
                        created_at=parse_datetime(fields['created_at'])
                    )
            for entry_id, node_ids in journal_mentions.items():
                JournalEntry.objects.get(pk=entry_id).mentioned_nodes.set(node_ids)

            for row in by_model.get('main.journalimage', []):
                fields = row['fields']
                image = JournalImage.objects.create(
                    pk=row['pk'],
                    entry_id=fields.get('entry'),
                    image=fields['image'],
                )
                if fields.get('uploaded_at'):
                    JournalImage.objects.filter(pk=image.pk).update(
                        uploaded_at=parse_datetime(fields['uploaded_at'])
                    )

            for row in by_model.get('main.alertaction', []):
                fields = row['fields']
                action = AlertAction.objects.create(
                    pk=row['pk'],
                    alert_id=fields['alert_id'],
                    alert_type=fields.get('alert_type', ''),
                    node_id=fields.get('node'),
                    title=fields.get('title', ''),
                    action=fields.get('action', 'dismissed'),
                    outcome=fields.get('outcome', ''),
                    owner=user,
                )
                if fields.get('created_at'):
                    AlertAction.objects.filter(pk=action.pk).update(
                        created_at=parse_datetime(fields['created_at'])
                    )

            settings_rows = by_model.get('main.appsettings', [])
            if settings_rows:
                root_node_id = settings_rows[0]['fields'].get('root_node')
                if root_node_id and Node.objects.filter(pk=root_node_id).exists():
                    user.root_node_id = root_node_id
                    user.save(update_fields=['root_node'])

        self.stdout.write(
            self.style.SUCCESS(
                f'Imported legacy data for {user.username}: '
                f'{Node.objects.count()} nodes, {Relationship.objects.count()} relationships, '
                f'{JournalEntry.objects.count()} journal entries.'
            )
        )
