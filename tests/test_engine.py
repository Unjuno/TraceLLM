import time

import pytest
import torch
from filelock import Timeout

from trace_llm.engine import Engine, Conflict, Missing
from trace_llm.model import Config


@pytest.fixture
def engine(tmp_path):
    torch.set_num_threads(1)
    e = Engine(tmp_path, config=Config(context=32), max_steps=8)
    yield e
    e.close()


def page(e, text='これは人工のテスト文章です。'):
    return e.create_page('Test', text)


def feed(e, p, key='one', repeats=1):
    return e.feed(p['id'], p['revision'], repeats, key)


def test_no_training_on_save(engine):
    before = engine.status()['checkpoint_id']
    p = page(engine)
    engine.update_page(p['id'], 'edited', '変更', p['revision'])
    assert engine.status()['checkpoint_id'] == before
    assert engine.history()['jobs'] == []


def test_optimistic_edits(engine):
    p = page(engine)
    q = engine.update_page(p['id'], 'new', '内容', p['revision'])
    assert q['revision'] == 2
    with pytest.raises(Conflict):
        engine.update_page(p['id'], 'bad', '古い', p['revision'])


def test_feed_freezes_version_and_deduplicates(engine):
    p = page(engine); j = feed(engine, p)
    engine.update_page(p['id'], 'new', '新しい内容', p['revision'])
    assert feed(engine, p)['id'] == j['id']
    with pytest.raises(Conflict):
        feed(engine, p, repeats=2)
    engine.train_next()
    job = engine.history()['jobs'][0]
    assert job['status'] == 'succeeded'
    assert job['revision'] == 1
    assert job['metrics']['new_targets'] == len(p['body'].encode()) + 1
    assert job['metrics']['loss_after'] < job['metrics']['loss_before']


def test_short_page_and_repeat(engine):
    p = page(engine, 'あ'); feed(engine, p, repeats=3)
    engine.train_next()
    j = engine.history()['jobs'][0]
    assert j['metrics']['new_targets'] == 12
    assert j['metrics']['capped'] is False
    feed(engine, p, key='explicit-repeat'); engine.train_next()
    assert len(engine.history()['jobs']) == 2


def test_empty_page_not_trainable(engine):
    p = page(engine, '')
    with pytest.raises(ValueError):
        feed(engine, p)


def test_restore_branch_replay_and_pending_conflict(engine):
    root = engine.status()['checkpoint_id']
    a = page(engine, 'Aの資料です。'); feed(engine, a); engine.train_next()
    learned = engine.status()['checkpoint_id']
    b = page(engine, 'Bの資料です。'); job = feed(engine, b, 'two')
    with pytest.raises(Conflict):
        engine.restore(root)
    engine.cancel(job['id'])
    result = engine.restore(root)
    assert result['parent_id'] == root
    assert result['id'] not in {root, learned}
    payload = engine.load_current()
    assert payload['learned'] == {}
    feed(engine, b, 'three'); engine.train_next()
    assert engine.history()['jobs'][0]['metrics']['replay_targets'] == 0


def test_generation_does_not_train_and_retains_history(engine):
    p = page(engine); before = engine.status()['checkpoint_id']
    r = engine.respond(p['id'], p['revision'], 8, 0.0, 17)
    engine.respond(p['id'], p['revision'], 8, 0.0, 17)
    assert r['checkpoint_id'] == before
    assert r['raw_tokens'] is not None
    assert len(engine.responses(p['id'])) == 2
    assert engine.status()['checkpoint_id'] == before
    assert engine.history()['jobs'] == []


def test_retention_keeps_current(engine):
    root = engine.status()['checkpoint_id']
    p = page(engine); feed(engine, p); engine.train_next()
    current = engine.status()['checkpoint_id']
    engine.prune(time.time() + 8*86400)
    assert engine.status()['checkpoint_id'] == current
    assert engine.load_current()['model']
    with pytest.raises(Missing):
        engine.restore(root)


def test_restart_keeps_queue_and_weights(tmp_path):
    e = Engine(tmp_path, config=Config(context=32)); p = page(e)
    feed(e, p); root = e.status()['checkpoint_id']; e.close()
    e = Engine(tmp_path, config=Config(context=32))
    try:
        assert e.status()['checkpoint_id'] == root
        e.train_next()
        assert e.history()['jobs'][0]['status'] == 'succeeded'
    finally:
        e.close()


def test_single_process_lock(engine):
    with pytest.raises(Timeout):
        Engine(engine.root)


def test_delete_does_not_claim_unlearning(engine):
    p = page(engine); feed(engine, p); engine.train_next()
    before = engine.status()['checkpoint_id']
    engine.delete_page(p['id'])
    assert engine.pages() == []
    assert engine.status()['checkpoint_id'] == before
    with pytest.raises(Missing):
        engine.get_page(p['id'])


def test_capped_job_reports_partial_exposure(engine):
    engine.max_steps = 1
    p = page(engine, '検証文章。' * 100)
    feed(engine, p, repeats=2); engine.train_next()
    m = engine.history()['jobs'][0]['metrics']
    assert m['capped'] and m['new_targets'] == 32
    assert m['new_targets'] < m['requested_new_targets']


def test_failed_publication_keeps_previous_model(engine, monkeypatch):
    before = engine.status()['checkpoint_id']
    p = page(engine); feed(engine, p)
    def fail(*args, **kwargs):
        raise OSError('Simulated storage failure')
    monkeypatch.setattr(engine, '_publish', fail)
    engine.train_next()
    assert engine.status()['checkpoint_id'] == before
    assert engine.history()['jobs'][0]['status'] == 'failed'


def test_checkpoint_tampering_rejected(engine):
    path = engine.checkpoint_dir / (engine.status()['checkpoint_id'] + '.pt')
    path.write_bytes(b'corrupted')
    with pytest.raises(RuntimeError, match='integrity'):
        engine.load_current()


def test_expired_restore_refused_without_pruning(engine):
    root = engine.status()['checkpoint_id']
    p = page(engine); feed(engine, p); engine.train_next()
    with engine.store.connect(True) as db:
        db.execute('UPDATE checkpoints SET expires=0 WHERE id=?', (root,))
    with pytest.raises(Missing):
        engine.restore(root)


def test_replay_uses_only_previously_fed_pages(engine):
    a = page(engine, '最初の人工資料。'); feed(engine,a); engine.train_next()
    page(engine, '保存しただけの未投入資料。')
    b = page(engine, '新しい人工資料。'); feed(engine,b,'b'); engine.train_next()
    m = engine.history()['jobs'][0]['metrics']
    assert m['replay_targets'] > 0
    assert len(engine.load_current()['learned']) == 2


def test_interrupted_job_failed_not_duplicated(tmp_path):
    e = Engine(tmp_path); p = page(e); j = feed(e,p)
    root = e.status()['checkpoint_id']
    with e.store.connect(True) as db:
        db.execute("UPDATE jobs SET status='running' WHERE id=?",(j['id'],))
    e.close()
    e = Engine(tmp_path)
    try:
        assert e.history()['jobs'][0]['status'] == 'failed'
        assert not e.train_next()
        assert e.status()['checkpoint_id'] == root
    finally:
        e.close()


def test_restore_serializes_new_feed(engine, monkeypatch):
    import threading
    root = engine.status()['checkpoint_id']
    p = page(engine)
    entered, release, fed = threading.Event(), threading.Event(), threading.Event()
    publish = engine._publish
    errors = []
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return publish(*args, **kwargs)
    monkeypatch.setattr(engine, '_publish', paused)
    def restoring():
        try:
            engine.restore(root)
        except Exception as exc:
            errors.append(exc)
    def feeding():
        try:
            feed(engine, p); fed.set()
        except Exception as exc:
            errors.append(exc)
    a = threading.Thread(target=restoring); a.start(); assert entered.wait(5)
    b = threading.Thread(target=feeding); b.start()
    try:
        assert not fed.wait(0.2), 'feed raced with a restore in progress'
    finally:
        release.set(); a.join(5); b.join(5)
    assert not errors and fed.is_set()


def test_repeat_marked_as_previously_seen(engine):
    p = page(engine);feed(engine,p);engine.train_next()
    assert not engine.history()['jobs'][0]['metrics']['content_seen_before']
    feed(engine,p,'second');engine.train_next()
    assert engine.history()['jobs'][0]['metrics']['content_seen_before']


def test_new_update_preserves_stale_current_for_seven_more_days(engine):
    root = engine.status()['checkpoint_id']
    with engine.store.connect(True) as db:
        db.execute('UPDATE checkpoints SET expires=0 WHERE id=?', (root,))
    p = page(engine);feed(engine,p);engine.train_next()
    engine.prune()
    old = next(c for c in engine.history()['checkpoints'] if c['id']==root)
    assert old['restorable']
    assert old['expires'] > time.time() + 6.9*86400


def test_nonfinite_training_does_not_publish(engine, monkeypatch):
    from trace_llm.model import GPT
    root=engine.status()['checkpoint_id']
    p=page(engine);feed(engine,p)
    monkeypatch.setattr(GPT,'loss',lambda *args: torch.tensor(float('nan'),requires_grad=True))
    engine.train_next()
    assert engine.status()['checkpoint_id']==root
    assert engine.history()['jobs'][0]['status']=='failed'
