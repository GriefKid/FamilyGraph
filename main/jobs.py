"""Tiny background-job runner (no Celery).

    job = start_job(user, 'persona-batch', worker)
    # worker(job) runs on a daemon thread; call job.set_progress(dict) inside.

Jobs are recorded in BackgroundJob so the UI can poll /api/jobs/<id>/.
Only for short, best-effort work — nothing that must survive a restart.
"""
import logging
import threading
from datetime import timedelta

from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)


class _JobHandle:
    def __init__(self, record_id):
        self.id = record_id

    def set_progress(self, data):
        from .models import BackgroundJob
        BackgroundJob.objects.filter(pk=self.id).update(progress=dict(data or {}))

    def finish(self, result='', status='done'):
        from .models import BackgroundJob
        BackgroundJob.objects.filter(pk=self.id).update(
            status=status, result=str(result)[:400], finished_at=timezone.now(),
        )


def start_job(user, kind, worker, *, single=True, sync=False):
    """Create a BackgroundJob and run ``worker(handle)`` on a daemon thread.

    ``single`` keeps one running job of this kind per user (a cache lock);
    if one is already running its record is returned instead.
    ``sync=True`` runs the worker inline (for tests / management commands).
    """
    from .models import BackgroundJob

    lock_key = f'job-lock:{user.id}:{kind}'
    if single:
        running = BackgroundJob.objects.filter(
            owner=user, kind=kind, status='running',
        ).order_by('-created_at').first()
        if running:
            if running.created_at and running.created_at >= timezone.now() - timedelta(minutes=30):
                return running
            running.status = 'error'
            running.result = 'کار قبلی بیش از ۳۰ دقیقه بدون پاسخ ماند و بسته شد.'
            running.finished_at = timezone.now()
            running.save(update_fields=['status', 'result', 'finished_at'])
        if not cache.add(lock_key, '1', 60 * 30):
            existing = BackgroundJob.objects.filter(owner=user, kind=kind).first()
            if existing:
                return existing

    record = BackgroundJob.objects.create(owner=user, kind=kind, progress={'running': True})
    handle = _JobHandle(record.id)
    uid = user.id

    def _run():
        try:
            worker(handle)
            if BackgroundJob.objects.filter(pk=record.id, status='running').exists():
                handle.finish(status='done')
        except Exception as exc:  # noqa: BLE001 - report, don't crash the thread
            logger.warning('background job %s (%s) failed: %s', record.id, kind, exc)
            handle.finish(result=str(exc)[:400], status='error')
        finally:
            cache.delete(lock_key)
            if not sync:
                close_old_connections()

    if sync:
        _run()
        record.refresh_from_db()
        return record

    try:
        threading.Thread(target=_run, name=f'job-{kind}-{uid}', daemon=True).start()
    except Exception:
        cache.delete(lock_key)
        handle.finish(result='نتوانستم کار پس‌زمینه را شروع کنم', status='error')
    return record
