"""Executable contract for the durable training-job state machine."""
import sqlite3

import pytest
import torch

from trace_llm.engine import Engine, Conflict
from trace_llm.model import Config


def test_transition_matrix_is_closed():
    from trace_llm.lifecycle import next_job_state, InvalidTransition

    for start, end in [('queued', 'running'), ('queued', 'cancelled'),
                       ('running', 'succeeded'), ('running', 'failed')]:
        assert next_job_state(start, end) == end
    for start, end in [('succeeded', 'running'), ('failed', 'queued'),
                       ('cancelled', 'running'), ('queued', 'succeeded'),
                       ('running', 'cancelled'), ('queued', 'failed')]:
        with pytest.raises(InvalidTransition):
            next_job_state(start, end)


def test_claim_and_transition_require_matching_stored_state(tmp_path):
    from trace_llm.lifecycle import transition_job, InvalidTransition

    torch.set_num_threads(1)
    engine = Engine(tmp_path, config=Config(context=32))
    try:
        page = engine.create_page('a', '訓練用の人工文章')
        job = engine.feed(page['id'], page['revision'], 1, 'idempotent')
        with engine.store.connect(True) as db:
            transition_job(db, job['id'], 'queued', 'running', started=1)
            with pytest.raises(InvalidTransition):
                transition_job(db, job['id'], 'queued', 'running', started=2)
            transition_job(db, job['id'], 'running', 'failed', finished=3)
        assert engine.history()['jobs'][0]['status'] == 'failed'
    finally:
        engine.close()


def test_deletion_after_last_training_step_blocks_publication(tmp_path, monkeypatch):
    torch.set_num_threads(1)
    engine = Engine(tmp_path, config=Config(context=32), max_steps=2)
    try:
        page = engine.create_page('a', 'ここに書かれた文章は人工的な試験データです。')
        job = engine.feed(page['id'], page['revision'], 1, 'one')
        old_id = engine.status()['checkpoint_id']
        old_publish = engine._publish

        def concurrent_deletion(*args, **kwargs):
            if args[3] == 'train':
                engine.delete_page(page['id'])
            return old_publish(*args, **kwargs)

        monkeypatch.setattr(engine, '_publish', concurrent_deletion)
        assert engine.train_next()
        assert engine.status()['checkpoint_id'] == old_id
        rows = engine.store.rows('SELECT status FROM jobs WHERE id=?', (job['id'],))
        assert rows[0]['status'] == 'failed'
        assert len(list(engine.checkpoint_dir.glob('*.pt'))) == 1
    finally:
        engine.close()


def test_failed_publication_does_not_leave_orphan_checkpoint(tmp_path, monkeypatch):
    torch.set_num_threads(1)
    engine = Engine(tmp_path, config=Config(context=32))
    try:
        payload = engine.load_current()
        current = engine.status()['checkpoint_id']
        original = set(engine.checkpoint_dir.glob('*.pt'))

        def write_fails(*args, **kwargs):
            raise sqlite3.OperationalError('artificial database failure')

        monkeypatch.setattr(engine.store, 'connect', write_fails)
        with pytest.raises(sqlite3.OperationalError):
            engine._publish(payload, current, current, 'restore')
        assert set(engine.checkpoint_dir.glob('*.pt')) == original
    finally:
        # avoid a patched connect during close (lock release only)
        engine.close()


def test_checkpoint_state_is_explicit_and_current_survives_expiry():
    from trace_llm.lifecycle import checkpoint_state

    now = 100.0
    assert checkpoint_state(1, True, 1.0, now)=='current'
    assert checkpoint_state(1, False, 101.0, now)=='restorable'
    assert checkpoint_state(1, False, 100.0, now)=='expired'
    assert checkpoint_state(0, False, 101.0, now)=='unavailable'
    assert checkpoint_state(0, True, 101.0, now)=='unavailable'


def test_deleted_page_cancels_queued_jobs_only(tmp_path):
    torch.set_num_threads(1)
    e=Engine(tmp_path,config=Config(context=32))
    try:
        p=e.create_page('人工テスト','これはテスト専用の文章です')
        j=e.feed(p['id'], p['revision'], 1,'delete')
        e.delete_page(p['id'])
        assert e.store.rows('SELECT status FROM jobs WHERE id=?',(j['id'],))[0]['status']=='cancelled'
        assert not e.train_next()
    finally:
        e.close()


def test_background_worker_failure_is_reported_not_silently_lost(tmp_path, monkeypatch):
    """If maintenance fails, a queued job must not look as if it is progressing."""
    import time

    torch.set_num_threads(1)
    engine = Engine(tmp_path, config=Config(context=32))
    try:
        page = engine.create_page('synthetic', '単純な人工テスト文章')
        engine.feed(page['id'], page['revision'], 1, 'worker-failure')

        def bad_prune():
            raise RuntimeError('synthetic maintenance failure')

        monkeypatch.setattr(engine, 'prune', bad_prune)
        engine.start()
        deadline = time.monotonic() + 3
        while engine._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not engine._thread.is_alive()
        status = engine.status()
        assert status['worker']['state'] == 'failed'
        assert status['worker']['error'] == 'RuntimeError'
        assert status['jobs']['queued'] == 1
    finally:
        engine.close()
