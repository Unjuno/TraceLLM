"""Read-only health observations, using disposable artificial pages only."""
import json
import threading
import time

import pytest
import torch
from fastapi.testclient import TestClient

from trace_llm.app import create_app
from trace_llm.engine import Engine
from trace_llm.model import Config


@pytest.fixture
def engine(tmp_path):
    torch.set_num_threads(1)
    e = Engine(tmp_path, config=Config(context=32))
    yield e
    e.close()


def health(e):
    from trace_llm.health import summarize_health
    return summarize_health(e.store)


def train(e, text='人工の文です。', key='a'):
    p = e.create_page('テスト用', text)
    j = e.feed(p['id'], 1, 1, key)
    e.train_next()
    assert e.history()['jobs'][0]['status'] == 'succeeded'
    return p, j


def test_empty_health_is_not_a_claim_of_learning(engine):
    h = health(engine)
    assert h['execution']['state'] == 'not_started'
    assert h['generalization']['status'] == 'unavailable'
    assert h['retention']['status'] == 'unavailable'
    assert h['observations'] == []
    assert h['exposure']['new_targets'] == 0
    assert h['exposure']['complete'] is True
    assert h['scope']['branch'] == 'current_ancestry'


def test_first_seen_and_repetition_are_separate(engine):
    p, _ = train(engine)
    engine.feed(p['id'], 1, 1, 'repeat'); engine.train_next()
    h = health(engine)
    assert h['execution']['state'] == 'ready'
    assert len(h['observations']) == 2
    assert len(h['first_seen']) == 1
    assert h['generalization']['status'] == 'unavailable'
    assert h['exposure']['new_targets'] == 2 * (len(p['body'].encode()) + 1)
    assert h['exposure']['replay_targets'] == 0
    assert h['latest_timing']['scope'].endswith('excludes checkpoint publication')
    assert 'process_cpu_seconds' in h['latest_timing']
    assert p['body'] not in json.dumps(h, ensure_ascii=False)


def test_retention_reports_a_matched_sample_not_intelligence(engine):
    train(engine, key='first')
    train(engine, '次の人工文章を学びます。', key='second')
    h = health(engine)
    r = h['retention']
    assert r['status'] == 'measured' and r['sample_pages'] == 1
    assert r['delta'] == pytest.approx(r['loss_after'] - r['loss_before'])
    assert h['generalization']['status'] == 'unavailable'
    assert len(h['first_seen']) == 2


def test_restored_branch_excludes_future_checkpoints(engine):
    root = engine.status()['checkpoint_id']
    train(engine)
    abandoned = engine.status()['checkpoint_id']
    engine.restore(root)
    h = health(engine)
    assert h['observations'] == [] and h['exposure']['new_targets'] == 0
    train(engine, '分岐した人工文章。', key='branch')
    h = health(engine)
    assert len(h['observations']) == 1
    assert all(o['checkpoint_id'] != abandoned for o in h['observations'])


def test_seven_days_and_checkpoint_availability_are_distinct(engine):
    train(engine)
    with engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET created=?, started=?, finished=?', (time.time()-8*86400,)*3)
    h = health(engine)
    assert h['observations'] == [] and h['exposure']['new_targets'] > 0
    assert h['recovery']['current_preserved'] is True


def test_legacy_metrics_are_unknown_not_zero(engine):
    train(engine)
    with engine.store.connect(True) as db:
        db.execute("UPDATE jobs SET metrics='{}'")
    h = health(engine)
    assert h['exposure']['new_targets'] is None and not h['exposure']['complete']
    assert h['retention']['status'] == 'unavailable'
    assert h['first_seen'] == []


def test_nonfinite_or_corrupt_metrics_do_not_escape_as_json_nan(engine):
    train(engine)
    with engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=?', ('{"loss_before":NaN,"loss_after":Infinity}',))
    h = health(engine)
    json.dumps(h, allow_nan=False)
    assert h['observations'][0]['loss_before'] is None
    with engine.store.connect(True) as db:
        db.execute("UPDATE jobs SET metrics='invalid-json'")
    json.dumps(health(engine), allow_nan=False)


def test_failed_attempt_does_not_create_learning_or_hide_last_model(engine):
    p = engine.create_page('人工', '失敗時の検査用。')
    j = engine.feed(p['id'], 1, 1, 'fail')
    with engine.store.connect(True) as db:
        db.execute("UPDATE jobs SET status='failed', finished=?, error='failure' WHERE id=?",
                   (time.time(), j['id']))
    h = health(engine)
    assert h['execution']['state'] == 'attention' and h['execution']['recent_counts']['failed'] == 1
    assert h['observations'] == [] and h['recovery']['current_preserved']


def test_partial_update_is_not_reported_as_completed_exposure(engine):
    engine.max_steps = 1
    p, _ = train(engine, '長い人工の文章です。' * 15)
    h = health(engine)
    o = h['observations'][0]
    assert o['capped'] and o['stop_reason'] == 'step_safety_cap'
    assert o['new_targets'] < o['requested_new_targets']


def test_does_not_load_model_or_wait_for_training_lock(engine, monkeypatch):
    monkeypatch.setattr(engine, '_load', lambda *_: pytest.fail('health must not load weights'))
    results = []
    with engine._lock:
        thread = threading.Thread(target=lambda: results.append(health(engine)))
        thread.start(); thread.join(timeout=3)
        completed = not thread.is_alive()
    thread.join(timeout=3)
    assert completed and results


def test_health_api_is_read_only_and_local(tmp_path):
    torch.set_num_threads(1)
    with TestClient(create_app(tmp_path, worker=False), base_url='http://127.0.0.1') as client:
        before = client.get('/api/status').json()['checkpoint_id']
        r = client.get('/api/learning-health')
        assert r.status_code == 200
        assert r.json()['checkpoint_id'] == before
        assert client.get('/api/history').json()['jobs'] == []
        assert client.get('/api/status').json()['checkpoint_id'] == before
        assert 'no-store' in r.headers['cache-control']


def test_missing_current_file_is_not_healthy(engine):
    cp = engine.status()['checkpoint_id']
    (engine.checkpoint_dir / (cp + '.pt')).unlink()
    h = health(engine)
    assert h['execution']['state'] == 'attention'
    assert h['recovery']['current_preserved'] is False


def test_limited_series_reports_truncation(engine, monkeypatch):
    import trace_llm.health as module
    for i in range(3):
        train(engine, f'検証用の文章 {i}', key=str(i))
    monkeypatch.setattr(module, 'LIMIT', 2)
    h = health(engine)
    assert len(h['observations']) == 2
    assert h['scope']['truncated'] and h['scope']['total_observations'] == 3


def test_deleted_probe_is_not_claimed_as_a_measurement(engine):
    _, j = train(engine)
    m = {'retention': {'status': 'invalidated_by_deletion', 'page_ids': ['old'],
                      'loss_before': 1.0, 'loss_after': 2.0}}
    with engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?', (json.dumps(m), j['id']))
    r = health(engine)['retention']
    assert r['status'] == 'invalidated_by_deletion' and r['delta'] is None
