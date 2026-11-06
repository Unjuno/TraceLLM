"""Single-writer job lifecycle, enforced independently of UI presentation.

This is intentionally small: durable state lives in the existing SQLite v1
schema, not in an in-memory mutable state machine.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


ALLOWED = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


class InvalidTransition(RuntimeError):
    """The old state or the requested job transition is not valid."""


def next_job_state(current: str, target: str) -> str:
    try:
        source, dest = JobStatus(current), JobStatus(target)
    except ValueError as exc:
        raise InvalidTransition('Unknown training-job state') from exc
    if dest not in ALLOWED[source]:
        raise InvalidTransition(f'Illegal training-job transition: {source} -> {dest}')
    return str(dest)


def transition_job(db, job_id: str, source: str, target: str, **fields) -> None:
    """CAS a job state inside the *caller's* SQLite transaction.

    The update only succeeds if the stored state is still `source`. Only
    trusted internal fields are accepted; all values are bound parameters.
    """
    next_job_state(source, target)
    allowed_fields = {'started', 'finished', 'before_id', 'after_id', 'metrics', 'error'}
    if set(fields) - allowed_fields:
        raise ValueError('Unsupported training-job update field')
    assignments = ', '.join(['status=?'] + [f'{name}=?' for name in fields])
    values = [target, *fields.values(), job_id, source]
    result = db.execute(f'UPDATE jobs SET {assignments} WHERE id=? AND status=?', values)
    if result.rowcount != 1:
        raise InvalidTransition('Training-job state changed during operation')


def checkpoint_state(available: int, current: bool, expires: float, now: float) -> str:
    """Interpret the checkpoint retention lifecycle without modifying storage."""
    if not available:
        return 'unavailable'
    if current:
        return 'current'
    return 'restorable' if expires > now else 'expired'
