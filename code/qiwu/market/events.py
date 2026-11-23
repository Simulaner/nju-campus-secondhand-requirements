import json
import logging
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import Http404

from .models import AuditLog, Notification

logger = logging.getLogger(__name__)


class WorkflowConflict(ValidationError):
    pass


def record_action(actor, object_type, pk, action, *, failure=""):
    entry = AuditLog(
        actor=actor,
        object_type=object_type,
        object_id=pk,
        action=action,
        result="failure" if failure else "success",
        reason_code=failure,
    )
    try:
        entry.save(force_insert=True)
    except DatabaseError:
        if not failure:
            raise
        try:
            _append_failed_audit(entry)
        except OSError:
            logger.exception(
                "Could not preserve failed audit %s for %s %s", action, object_type, pk
            )


def _backup_path():
    return settings.BASE_DIR / ".audit-backup.jsonl"


def _append_failed_audit(entry):
    payload = {
        "id": str(entry.pk),
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "object_type": entry.object_type,
        "object_id": str(entry.object_id),
        "action": entry.action,
        "result": entry.result,
        "reason_code": entry.reason_code,
        "occurred_at": entry.occurred_at.isoformat(),
        "correlation_id": str(entry.correlation_id),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    with os.fdopen(os.open(_backup_path(), flags, 0o600), "ab") as backup:
        backup.write((json.dumps(payload) + "\n").encode("utf-8"))
        backup.flush()
        os.fsync(backup.fileno())


def archive_failed_audits():
    try:
        snapshot = _backup_path().read_bytes()
    except FileNotFoundError:
        return 0
    except OSError:
        logger.exception("Could not read the audit backup")
        return 0

    archived = 0
    for line in snapshot.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            # An append in progress is picked up on the next scan.
            continue
        try:
            fields = json.loads(line)
            pk = fields.pop("id")
            _, created = AuditLog.objects.get_or_create(pk=pk, defaults=fields)
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError, FieldError):
            logger.warning("Ignored an unreadable audit backup entry")
            continue
        archived += created
    return archived


def notify(recipient, object_type, pk, event_key, message):
    Notification.objects.create(
        recipient_id=recipient,
        object_type=object_type,
        object_id=pk,
        event_key=event_key,
        message=message,
    )


@contextmanager
def audited_transaction(actor, object_type, pk, action):
    try:
        with transaction.atomic():
            yield
    except (WorkflowConflict, Http404, PermissionDenied, DatabaseError) as exc:
        if isinstance(exc, WorkflowConflict):
            failure = exc.code or "state_conflict"
        elif isinstance(exc, Http404):
            failure = "not_found"
        elif isinstance(exc, PermissionDenied):
            failure = "not_allowed"
        else:
            failure = "write_failed"
        record_action(actor, object_type, pk, action, failure=failure)
        raise
