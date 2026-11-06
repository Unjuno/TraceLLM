"""Health UI contract. All texts and stores in these tests are synthetic."""
import json
import threading
import time

import pytest
import torch
from fastapi.testclient import TestClient

from trace_llm.app import create_app
from trace_llm.model import Config


@pytest.fixture
def client(tmp_path):
    torch.set_num_threads(1)
    app = create_app(tmp_path, worker=False, config=Config(context=32))
    with TestClient(app, base_url='http://127.0.0.1') as c:
        yield c


def train(client, body='人工のテスト文章。', key='feed', page=None):
    e = client.app.state.engine
    page = page or e.create_page('テスト専用の秘密タイトル', body)
    job = e.feed(page['id'], page['revision'], 2, key)
    e.train_next()
    return page, job


def snapshot(client, **params):
    response = client.get('/api/learning-health', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_health_is_not_claimed_good_or_zero_loss(client):
    h = snapshot(client)
    assert h['summary']['succeeded'] == 0
    assert h['quality']['independent_validation'] == 'unavailable'
    assert h['series']['first_seen'] == []
    assert h['series']['retention'] == []
    assert h['operation']['state'] == 'not_started'
    assert h['checkpoints']['restorable'] == 1


def test_feed_measurements_and_repeat_are_separate(client):
    page, _ = train(client)
    train(client, key='repeat', page=page)
    train(client, body='別の人工文章。', key='different')
    h = snapshot(client)
    assert h['summary']['succeeded'] == 3
    assert len(h['series']['first_seen']) == 2
    assert len(h['series']['retention']) == 1
    last = h['rows'][-1]
    assert last['retention_delta'] == pytest.approx(last['retention_after']-last['retention_before'])
    assert h['summary']['new_targets'] == sum(r['new_targets'] for r in h['rows'])
    assert h['summary']['replay_targets'] == sum(r['replay_targets'] for r in h['rows'])
    assert h['summary']['exposure_complete']
    assert h['quality']['independent_validation'] == 'unavailable'
    assert '人工のテスト文章' not in json.dumps(h, ensure_ascii=False)
    assert 'テスト専用の秘密タイトル' not in json.dumps(h, ensure_ascii=False)


def test_restore_uses_parent_not_previous_and_keeps_original_history(client):
    e = client.app.state.engine
    initial = e.status()['checkpoint_id']
    train(client, key='a')
    first = e.status()['checkpoint_id']
    train(client, body='二つ目の人工文章。', key='b')
    e.restore(first)
    train(client, body='新しい分岐の人工文章。', key='c')
    current = snapshot(client)
    all_branches = snapshot(client, scope='all')
    assert current['summary']['succeeded'] == 2
    assert all_branches['summary']['succeeded'] == 3
    assert current['current_checkpoint'] == e.status()['checkpoint_id']
    e.restore(initial)
    assert snapshot(client)['summary']['succeeded'] == 0
    assert snapshot(client, scope='all')['summary']['succeeded'] == 3


def test_legacy_missing_metrics_stay_null(client):
    _, j = train(client)
    e = client.app.state.engine
    with e.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?', (json.dumps({'loss_before':5.0,'loss_after':4.0}), j['id']))
    h = snapshot(client)
    row = h['rows'][0]
    assert row['scope'] == 'legacy_unknown'
    assert row['new_targets'] is None and row['elapsed_seconds'] is None
    assert h['summary']['exposure_complete'] is False
    assert h['series']['first_seen'] == []
    assert h['operation']['state'] == 'incomplete_metrics'


def test_nonfinite_metrics_do_not_break_json_or_appear_zero(client):
    _, j = train(client)
    e = client.app.state.engine
    with e.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?', (json.dumps({'loss_before':float('nan'), 'loss_after':float('inf'), 'new_targets':-1}), j['id']))
    h = snapshot(client)
    assert h['rows'][0]['loss_before'] is None
    assert h['rows'][0]['loss_after'] is None
    assert h['rows'][0]['new_targets'] is None
    assert h['operation']['state'] == 'incomplete_metrics'
    json.dumps(h, allow_nan=False)


def test_invalid_json_is_shown_not_silently_dropped(client):
    _, j = train(client)
    with client.app.state.engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?', ('{broken', j['id']))
    h = snapshot(client)
    assert h['rows'][0]['metrics_valid'] is False
    assert h['summary']['incomplete_metrics'] == 1


def test_window_counts_not_truncated_to_chart_limit(client):
    for i in range(4):
        train(client, body=f'人工のページ{i}。', key=str(i))
    h = snapshot(client, limit=2)
    assert h['summary']['succeeded'] == 4
    assert len(h['rows']) == 2
    assert h['selection']['total'] == 4 and h['selection']['truncated'] is True
    assert h['summary']['new_targets'] > sum(r['new_targets'] for r in h['rows'])
    e = client.app.state.engine
    old = h['rows'][-1]['job_id']
    with e.store.connect(True) as db:
        db.execute('UPDATE jobs SET created=? WHERE id=?', (time.time()-10*86400, old))
    assert snapshot(client)['summary']['succeeded'] == 3
    assert snapshot(client, days=30)['summary']['succeeded'] == 4


def test_expired_checkpoint_not_counted_except_current(client):
    train(client)
    e = client.app.state.engine
    with e.store.connect(True) as db:
        db.execute('UPDATE checkpoints SET expires=?', (time.time()-10,))
    h = snapshot(client)
    assert h['checkpoints']['restorable'] == 1
    assert len([p for p in h['checkpoint_options'] if p['restorable']]) == 1


def test_dashboard_reads_without_training_lock_or_weight_load(client, monkeypatch):
    e = client.app.state.engine
    locked = threading.Event(); release = threading.Event()
    def hold():
        with e._lock:
            locked.set(); release.wait(3)
    thread = threading.Thread(target=hold); thread.start(); locked.wait(1)
    def forbidden(*args, **kwargs): raise AssertionError('dashboard must not load weights')
    monkeypatch.setattr(e, '_load', forbidden)
    try:
        started = time.perf_counter(); snapshot(client)
        assert time.perf_counter()-started < 1
    finally:
        release.set();thread.join()


def test_read_only_and_pending_failed_status(client):
    e = client.app.state.engine
    page = e.create_page('人工', 'テストだけ。')
    j = e.feed(page['id'], 1, 1, 'pending')
    assert snapshot(client)['operation']['state'] == 'queued'
    current = e.status()['checkpoint_id']
    with e.store.connect(True) as db:
        db.execute("UPDATE jobs SET status='failed',before_id=?,error='simulated failure' WHERE id=?", (current,j['id']))
    before = e.history()
    h = snapshot(client)
    assert h['operation']['state'] == 'attention'
    assert h['summary']['failed'] == 1
    assert e.history() == before
    assert e.status()['checkpoint_id'] == current


@pytest.mark.parametrize('params', [{'scope':'bad'}, {'days':-1}, {'limit':0}, {'limit':501}, {'days':8}])
def test_health_query_validation(client, params):
    assert client.get('/api/learning-health', params=params).status_code == 422


def test_health_view_assets_are_local_and_have_semantic_controls(client):
    text = client.get('/').text
    assert 'id="healthNav"' in text
    assert 'id="healthView"' in text
    assert 'id="healthScope"' in text and 'id="healthDays"' in text
    assert 'id="healthCompare"' in text
    assert client.get('/static/health.js').status_code == 200
    assert client.get('/static/health.css').status_code == 200
    assert 'https://' not in text and 'http://' not in text


def test_no_recent_jobs_does_not_claim_trained_model_is_untrained(client):
    _, job = train(client)
    e = client.app.state.engine
    with e.store.connect(True) as db:
        db.execute('UPDATE jobs SET created=? WHERE id=?', (time.time()-10*86400, job['id']))
    h = snapshot(client)
    assert h['summary']['succeeded'] == 0
    assert h['operation']['state'] == 'idle'
    assert h['operation']['has_trained_lineage'] is True


def test_corrupt_optional_metadata_is_json_safe(client):
    _, j = train(client)
    with client.app.state.engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?', (json.dumps({'evaluation_protocol':float('nan'), 'timing_scope':float('inf'), 'new_targets':10**400}), j['id']))
    h = snapshot(client)
    assert h['rows'][0]['evaluation_protocol'] == 'legacy-unspecified'
    assert h['rows'][0]['new_targets'] is None
    json.dumps(h, allow_nan=False)


@pytest.mark.parametrize('scope_value', [[], {'kind': 'first_seen'}])
def test_corrupt_evaluation_scope_does_not_crash_dashboard(client, scope_value):
    _, job = train(client)
    with client.app.state.engine.store.connect(True) as db:
        db.execute('UPDATE jobs SET metrics=? WHERE id=?',
                   (json.dumps({'evaluation_scope': scope_value}), job['id']))
    h = snapshot(client)
    assert h['rows'][0]['scope'] == 'legacy_unknown'
    assert h['summary']['incomplete_metrics'] == 1
    assert h['series']['first_seen'] == []
