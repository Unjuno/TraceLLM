import copy
from dataclasses import asdict
import time

import pytest
import torch

from trace_llm.engine import Engine
from trace_llm.model import Config
from trace_llm.training import TrainingConfig, TOKENIZER


@pytest.fixture
def engine(tmp_path):
    torch.set_num_threads(1)
    e = Engine(tmp_path, config=Config(context=32))
    yield e
    e.close()


def train(e, body, key='job'):
    p = e.create_page('人工テスト', body)
    e.feed(p['id'], 1, 2, key)
    assert e.train_next()
    assert e.history()['jobs'][0]['status'] == 'succeeded'
    return p, e.history()['jobs'][0]['metrics']


def test_first_seen_repeat_and_edited_evaluation(engine):
    p, m = train(engine, '人工文章だけ。')
    assert m['prequential_loss'] == m['loss_before']
    assert m['evaluation_scope'] == 'first_seen_page_exact_hash_only'
    engine.feed(p['id'], 1, 1, 'repeat'); engine.train_next()
    m = engine.history()['jobs'][0]['metrics']
    assert m['prequential_loss'] is None and m['evaluation_scope'] == 'repeated_body'
    edited = engine.update_page(p['id'], '人工', '編集した人工文章だけ。', 1)
    engine.feed(p['id'], edited['revision'], 1, 'edit'); engine.train_next()
    m = engine.history()['jobs'][0]['metrics']
    assert m['prequential_loss'] is None and m['evaluation_scope'] == 'edited_page'


def test_identical_text_in_new_page_is_not_unseen(engine):
    train(engine, '繰り返す人工文章。')
    _, m = train(engine, '繰り返す人工文章。', 'duplicate')
    assert m['prequential_loss'] is None
    assert m['first_seen_body_bytes'] == 0


def test_retention_probe_excludes_saved_only_deleted_and_future_branch(engine):
    root = engine.status()['checkpoint_id']
    old, _ = train(engine, '過去に投入した人工文章。')
    private = engine.create_page('未投入', '保存しただけの文。')
    _, m = train(engine, '新しい人工文章。', 'new')
    assert m['retention']['page_ids'] == [old['id']]
    assert m['retention']['loss_before'] is not None
    assert private['id'] not in m['retention']['page_ids']
    engine.delete_page(old['id'])
    _, m = train(engine, '次の人工文章。', 'next')
    assert old['id'] not in m['retention']['page_ids']
    engine.restore(root)
    _, m = train(engine, '分岐先の人工文章。', 'branch')
    assert m['retention']['page_ids'] == [] and m['replay_targets'] == 0


def test_policy_and_exposure_persist_and_resume(tmp_path):
    policy = TrainingConfig(replay_ratio=0.5, context_prefix=4, learning_rate=1e-3)
    e = Engine(tmp_path, config=Config(context=32), training=policy)
    train(e, '最初の人工文章。')
    cp = e.status()['checkpoint_id']; saved = e.load_current()
    assert saved['training'] == asdict(policy) and saved['tokenizer'] == TOKENIZER
    assert saved['exposure_totals']['complete']
    e.close()
    e = Engine(tmp_path)
    try:
        assert e.training == policy and e.status()['checkpoint_id'] == cp
        _, m = train(e, '次の人工文章。', 'next')
        assert m['learning_rate'] == 1e-3
        assert m['new_targets'] == (len('次の人工文章。'.encode()) + 1) * 2
    finally:
        e.close()
    with pytest.raises(ValueError, match='training'):
        Engine(tmp_path, training=TrainingConfig())


def test_legacy_payload_continues_without_resetting_optimizer(engine):
    train(engine, '人工文章。')
    payload = engine.load_current()
    for key in ('training', 'tokenizer', 'exposure_totals'):
        payload.pop(key, None)
    current = engine.status()['checkpoint_id']
    engine._publish(payload, current, current, 'legacy-fixture')
    steps = [v['step'].item() for v in payload['optimizer']['state'].values()]
    train(engine, '別の人工文章。', 'after-legacy')
    updated = engine.load_current()
    assert min(v['step'].item() for v in updated['optimizer']['state'].values()) > max(steps)
    assert not updated['exposure_totals']['complete']
    assert updated['tokenizer'] == TOKENIZER


def test_restore_replays_same_next_update_exactly(engine):
    train(engine, '以前の人工文章。')
    checkpoint = engine.status()['checkpoint_id']
    p = engine.create_page('次', 'これは次の人工文章です。')
    engine.feed(p['id'], 1, 2, 'first'); engine.train_next()
    a = copy.deepcopy(engine.load_current())
    engine.restore(checkpoint)
    engine.feed(p['id'], 1, 2, 'second'); engine.train_next()
    b = engine.load_current()
    assert all(torch.equal(a['model'][k], b['model'][k]) for k in a['model'])
    assert a['sampler_rng'] == b['sampler_rng']
    assert a['exposure_totals'] == b['exposure_totals']


def test_soft_time_budget_reports_partial_not_full_epoch(tmp_path):
    e = Engine(tmp_path, config=Config(context=16),
               training=TrainingConfig(max_train_seconds=1e-9))
    try:
        _, m = train(e, '人工文章。' * 100)
        assert m['updates'] == 1 and m['capped']
        assert m['stop_reason'] == 'time_budget'
        assert m['new_targets'] < m['requested_new_targets']
        assert m['elapsed_seconds'] >= 0 and m['process_cpu_seconds'] >= 0
    finally:
        e.close()


def test_generation_at_previous_checkpoint_does_not_change_current(engine):
    root = engine.status()['checkpoint_id']
    p, _ = train(engine, '人工文章。')
    current = engine.status()['checkpoint_id']
    response = engine.respond(p['id'], 1, 4, 0.0, 42, checkpoint_id=root)
    assert response['checkpoint_id'] == root
    assert engine.status()['checkpoint_id'] == current
    assert response['metadata']['untrained']


def test_exposure_accounting_and_evaluation_export(engine):
    _, m = train(engine, '人工文章。')
    assert m['replay_actual_ratio'] == 0
    assert m['first_seen_body_bytes'] == len('人工文章。'.encode())
    assert m['stop_reason'] == 'requested_targets_completed'
    result = engine.evaluations()
    assert len(result['observations']) == 1
    assert result['independent_validation'] == 'unavailable'


def test_duty_cycle_wait_is_cooperative(tmp_path, monkeypatch):
    e = Engine(tmp_path, config=Config(context=32), training=TrainingConfig(duty_cycle=0.5))
    waits = []
    monkeypatch.setattr(e._stop, 'wait', lambda delay: waits.append(delay) or False)
    try:
        train(e, '人工文章。' * 4)
        assert waits and all(t > 0 for t in waits)
    finally:
        e.close()
