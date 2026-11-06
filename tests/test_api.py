import time

import pytest
from fastapi.testclient import TestClient
from trace_llm.app import create_app

HEADERS = {'X-Trace-Request': '1'}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, worker=False), base_url='http://localhost') as c:
        yield c


def create(c, body='これは検証用のページです。'):
    result=c.post('/api/pages',json={'title':'Test','body':body},headers=HEADERS)
    assert result.status_code == 201
    return result.json()


def test_mutation_guard(client):
    assert client.post('/api/pages',json={'title':'x'}).status_code == 403
    assert client.post('/api/pages',json={'title':'x'},
                       headers={**HEADERS,'Origin':'https://evil.example'}).status_code == 403


def test_dns_rebinding_host_rejected(client):
    assert client.get('/api/status',headers={'Host':'evil.example'}).status_code == 400


def test_page_api_conflict_and_empty_feed(client):
    p=create(client,'')
    assert client.post('/api/feed',headers=HEADERS,json={
        'page_id':p['id'],'revision':1,'repeats':2,'request_key':'a'}).status_code == 422
    assert client.put('/api/pages/'+p['id'],headers=HEADERS,json={
        'title':'x','body':'new','revision':0}).status_code == 422


def test_feed_api_and_real_checkpoint(client):
    p=create(client)
    cp=client.get('/api/status').json()['checkpoint_id']
    j=client.post('/api/feed',headers=HEADERS,json={
        'page_id':p['id'],'revision':1,'repeats':2,'request_key':'a'})
    assert j.status_code == 202
    client.app.state.engine.train_next()
    h=client.get('/api/history').json()
    assert h['jobs'][0]['status'] == 'succeeded'
    assert h['current'] != cp
    r=client.post('/api/checkpoints/'+cp+'/restore',headers=HEADERS)
    assert r.status_code == 200 and r.json()['parent_id'] == cp


def test_response_metadata_and_no_auto_learning(client):
    p=create(client)
    r=client.post('/api/pages/'+p['id']+'/responses',headers=HEADERS,
                  json={'revision':1,'max_new_tokens':3}).json()
    assert len(r['raw_tokens']) <= 3 and r['metadata']['untrained']
    assert client.get('/api/history').json()['jobs'] == []


def test_request_validation(client):
    assert client.post('/api/pages',headers=HEADERS,
                       json={'body':'a'*70000}).status_code == 422
    assert client.post('/api/pages',headers=HEADERS,
                       json={'unknown':True}).status_code == 422


def test_static_ui_and_csp(client):
    r=client.get('/')
    assert r.status_code == 200 and 'ページ' in r.text
    assert 'default-src' in r.headers['Content-Security-Policy']
    assert client.get('/static/app.js').status_code == 200


def test_worker_really_executes(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://localhost') as c:
        p=create(c)
        c.post('/api/feed',headers=HEADERS,json={
            'page_id':p['id'],'revision':1,'request_key':'b','repeats':1})
        for _ in range(100):
            job=c.get('/api/history').json()['jobs'][0]
            if job['status'] in ('succeeded','failed'):
                break
            time.sleep(0.05)
        assert job['status'] == 'succeeded'


def test_configurable_new_store_and_evaluation_api(tmp_path):
    from trace_llm.model import Config
    from trace_llm.training import TrainingConfig
    with TestClient(create_app(tmp_path, worker=False, config=Config(context=512),
                               training=TrainingConfig(replay_ratio=0.5)),
                    base_url='http://localhost') as c:
        s = c.get('/api/status').json()
        assert s['config']['context'] == 512 and s['training']['replay_ratio'] == 0.5
        root = s['checkpoint_id']
        p = create(c)
        c.post('/api/feed', headers=HEADERS, json={
            'page_id':p['id'],'revision':1,'request_key':'test','repeats':1})
        c.app.state.engine.train_next()
        result = c.get('/api/evaluations').json()
        assert len(result['observations']) == 1
        assert p['body'] not in str(result)
        r = c.post('/api/pages/'+p['id']+'/responses', headers=HEADERS,
                   json={'revision':1,'max_new_tokens':2,'checkpoint_id':root})
        assert r.status_code == 201 and r.json()['checkpoint_id'] == root
