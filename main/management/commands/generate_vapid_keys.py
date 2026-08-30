"""Generate a VAPID key pair for Web Push, using `cryptography` only.

    python manage.py generate_vapid_keys

Prints the two lines to paste into .env. Safe to run anywhere.
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = 'Generate a VAPID public/private key pair for Web Push.'

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())

        private_raw = key.private_numbers().private_value.to_bytes(32, 'big')
        public_point = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )

        self.stdout.write(self.style.SUCCESS('# --- paste into .env ---'))
        self.stdout.write(f'VAPID_PRIVATE_KEY={_b64url(private_raw)}')
        self.stdout.write(f'VAPID_PUBLIC_KEY={_b64url(public_point)}')
        self.stdout.write('VAPID_ADMIN_EMAIL=you@example.com')
        self.stdout.write('')
        self.stdout.write('Then: pip install "pywebpush>=1.14"  and restart.')
