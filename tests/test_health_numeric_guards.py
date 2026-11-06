"""Invalid observations are unknown, never apparent learning or an API crash."""
import json

import pytest
import torch

from trace_llm.engine import Engine
from trace_llm.health import finite, retention_of, summarize_health
from trace_llm.model import Config


@pytest.mark.parametrize('value', [-1, -0.1, 10**400])
def test_invalid_nonnegative_measurement_is_missing(value):
    assert finite(value) is None


@pytest.mark.parametrize('value', [0, 0.0, 0.25, 10])
def test_valid_measurement_keeps_zero_and_precision(value):
    assert finite(value) == value


def test_negative_absolute_retention_loss_is_not_a_measurement():
    r = retention_of(None, {'retention': {'status': 'measured', 'page_ids': ['test'],
                                        'loss_before': 2.0, 'loss_after': -0.5}})
    assert r['status'] == 'unavailable'
    assert r['delta'] is None


def test_valid_negative_retention_delta_is_still_preserved():
    r = retention_of(None, {'retention': {'status': 'measured', 'page_ids': ['test'],
                                        'loss_before': 2.0, 'loss_after': 1.5}})
    assert r['status'] == 'measured'
    assert r['delta'] == -0.5


def test_malformed_persisted_numbers_do_not_crash_or_modify_state(tmp_path):
    torch.set_num_threads(1)
    engine = Engine(tmp_path, config=Config(context=32))
    try:
        page = engine.create_page('人工テスト', '入力検証専用。実際の日記ではありません。')
        job = engine.feed(page['id'], page['revision'], 1, 'numeric-guard')
        assert engine.train_next()
        before = engine.status()['checkpoint_id']
        with engine.store.connect(True) as db:
            m = json.loads(db.execute('SELECT metrics FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
            m.update(loss_before=-0.1, loss_after=10**400, prequential_loss=-0.1,
                     elapsed_seconds=10**400)
            raw = json.dumps(m)
            db.execute('UPDATE jobs SET metrics=? WHERE id=?', (raw, job['id']))
        result = summarize_health(engine.store)
        assert result['observations'][0]['loss_before'] is None
        assert result['observations'][0]['loss_after'] is None
        assert result['first_seen'] == []
        assert result['latest_timing'] is None
        assert result['generalization']['status'] == 'unavailable'
        json.dumps(result, allow_nan=False)
        assert engine.status()['checkpoint_id'] == before
        with engine.store.connect() as db:
            assert db.execute('SELECT metrics FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == raw
    finally:
        engine.close()
