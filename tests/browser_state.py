"""Offline component/API interaction contract: conflict must never destroy a draft.

Runs against real FastAPI TestClient and real JS/DOM under Chromium. It does
not verify HTTP routing, CSP, nor OS-specific runtime integration.
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
import torch
from trace_llm.app import STATIC, create_app


def main():
    torch.set_num_threads(1)
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',choices=('chromium','webkit'),default='chromium')
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    out=args.screenshot
    with tempfile.TemporaryDirectory(prefix='trace-state-ui-') as tmp:
        with TestClient(create_app(tmp,worker=False),base_url='http://127.0.0.1') as client:
            note = client.post('/api/pages',headers={'X-Trace-Request':'1'},json={
                'title':'実験の記録','body':'これはUI検証のための人工的な文章です。'}).json()
            failures={'responses':None,'legacyHistory':False}
            def bridge(source,url,options):
                if failures['responses'] and url == '/api/pages/'+failures['responses']+'/responses' and options.get('method','GET')=='GET':
                    return {'status':503,'text':json.dumps({'detail':'一時的な読込失敗'})}
                result=client.request(options.get('method','GET'),url,
                    headers=options.get('headers',{}),content=options.get('body'))
                if failures['legacyHistory'] and url == '/api/history' and result.status_code == 200:
                    payload=result.json()
                    payload['jobs'].insert(0,{'id':'old-history','title':'旧記録','created':0,
                        'revision':1,'repeats':1,'status':'succeeded','metrics':{}})
                    return {'status':200,'text':json.dumps(payload)}
                return {'status':result.status_code,'text':result.text}
            with sync_playwright() as p:
                options={}
                if args.browser=='chromium' and os.environ.get('TRACE_BROWSER_EXECUTABLE'):
                    options['executable_path']=os.environ['TRACE_BROWSER_EXECUTABLE']
                browser=getattr(p,args.browser).launch(**options)
                page=browser.new_page(viewport={'width':1440,'height':980})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                html=(STATIC/'index.html').read_text(encoding='utf-8')
                html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*>', '', html)
                html=re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', html)
                page.set_content(html)
                page.add_style_tag(content=(STATIC/'style.css').read_text(encoding='utf-8')+'\n'+(STATIC/'health.css').read_text(encoding='utf-8'))
                page.expose_binding('__traceHttp',bridge)
                page.evaluate('''() => { window.fetch = async (url, options={}) => {
                    const r=await window.__traceHttp(String(url),options);
                    return {ok:r.status>=200&&r.status<300,status:r.status,
                      json:async()=>JSON.parse(r.text)};
                }; }''')
                page.add_script_tag(content=(STATIC/'health.js').read_text(encoding='utf-8'))
                # The shipped app injects the pure transition module before app.js.
                if (STATIC/'ui_machine.js').exists():
                    page.add_script_tag(content=(STATIC/'ui_machine.js').read_text(encoding='utf-8'))
                page.add_script_tag(content=(STATIC/'app.js').read_text(encoding='utf-8'))
                expect(page.locator('#body')).to_have_value('これはUI検証のための人工的な文章です。')
                # A crashed background trainer must not look merely "queued".
                client.app.state.engine._worker_error = 'RuntimeError'
                page.evaluate('async () => { await poll(); }')
                expect(page.locator('#queueStatus')).to_contain_text('学習ワーカー停止')
                client.app.state.engine._worker_error = None
                page.evaluate('async () => { await poll(); }')
                assert not page.locator('#openSide').is_visible(), 'Duplicate hamburger in desktop sidebar layout'
                draft='画面上で入力した、まだ保存されていない文章です。'
                page.locator('#body').fill(draft)
                assert page.locator('#saveStatus').inner_text()=='未保存'
                # Another browser wrote revision 2 in the meantime.
                concurrent=client.put('/api/pages/'+note['id'],headers={'X-Trace-Request':'1'},json={
                    'title':'他の端末で編集','body':'競合したサーバー側の本文', 'revision':1})
                assert concurrent.status_code==200
                page.locator('#save').click()
                expect(page.locator('#saveConflict')).to_be_visible()
                assert page.locator('#saveConflict').evaluate('(e)=>getComputedStyle(e).borderTopStyle') != 'none', 'Recovery warning is visually discoverable'
                expect(page.locator('#body')).to_have_value(draft)
                expect(page.locator('#saveStatus')).to_contain_text('競合')
                if out:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out.with_name(out.stem+'-conflict'+out.suffix)),full_page=True)
                # Conflict recovery saves draft as a separate note, never overwrites revision 2.
                page.locator('#saveAsNew').click()
                expect(page.locator('#saveConflict')).to_be_hidden()
                expect(page.locator('#body')).to_have_value(draft)
                assert client.get('/api/pages/'+note['id']).json()['body']=='競合したサーバー側の本文'
                assert len(client.get('/api/pages').json())==2
                page.locator('#respond').click()
                expect(page.locator('#reveal')).to_be_visible()
                page.locator('#reveal').click()
                expect(page.locator('.response-text')).to_have_count(1)
                page.locator('#maskToggle').click()
                expect(page.locator('#reveal')).to_be_visible()
                if out:
                    page.screenshot(path=str(out),full_page=True)
                # A partial read must never bind an unseen page id to the old editor DOM.
                other=client.post('/api/pages',headers={'X-Trace-Request':'1'},json={
                    'title':'通信失敗時の別ページ','body':'別の文章の初期状態'}).json()
                page.evaluate('async () => { await refreshPages(); }')
                failures['responses']=other['id']
                page.locator('.page-row',has_text='通信失敗時の別ページ').click()
                expect(page.locator('#toast')).to_contain_text('一時的な読込失敗')
                # The open page remains the recovered draft, never partially switched.
                expect(page.locator('#body')).to_have_value(draft)
                page.locator('#body').fill('失敗後の編集も元のページに保存される')
                page.locator('#save').click()
                expect(page.locator('#saveStatus')).to_have_text('保存済み')
                assert client.get('/api/pages/'+other['id']).json()['body']=='別の文章の初期状態'
                assert client.get('/api/pages').json()[0]['body']=='失敗後の編集も元のページに保存される'
                failures['legacyHistory']=True
                page.locator('#historyNav').click()
                expect(page.locator('#historyContent')).to_contain_text('旧記録')
                expect(page.locator('#historyContent')).to_contain_text('計測なし')
                assert not errors, errors
                if out:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out.with_name(out.stem+'-history'+out.suffix)),full_page=True)
                # Deleting an edited note should first make the final draft an
                # immutable revision; soft deletion must not lose the draft.
                page.locator('#pagesNav').click()
                page_id=client.get('/api/pages').json()[0]['id']
                page.locator('#body').fill('削除前に保存されるべき未保存の文章')
                page.locator('.page-row.active .dots').click()
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('.page-dialog [data-delete]').click()
                expect(page.locator('#pageView')).to_be_hidden()
                latest=client.app.state.engine.store.rows(
                    'SELECT body FROM versions WHERE page_id=? ORDER BY revision DESC LIMIT 1',(page_id,))
                assert latest[0]['body']=='削除前に保存されるべき未保存の文章'
                browser.close()
    print('PASS: real component, concurrent edit, draft-preserving recovery, response masking')


if __name__=='__main__':main()
