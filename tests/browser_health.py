"""Desktop integration: real HTTP, synthetic notes only, no production data.

Run after `python -m playwright install chromium`.
Optionally: --browser webkit --artifacts /tmp/trace-health-artifacts
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect


HEADERS = {'X-Trace-Request': '1'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', choices=('chromium','firefox','webkit'), default='chromium')
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if args.artifacts:
        args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='trace-health-ui-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        url=f'http://127.0.0.1:{port}'
        env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
        log=Path(directory)/'server.txt'
        with log.open('w') as stream:
            process=subprocess.Popen([sys.executable,'-m','trace_llm','--data-dir',directory,
                                       '--port',str(port),'--threads','1'],env=env,
                                      stdout=stream,stderr=stream)
            try:
                with httpx.Client(base_url=url,timeout=15,trust_env=False) as client:
                    for _ in range(160):
                        try:
                            if client.get('/api/healthz').status_code==200: break
                        except httpx.TransportError: pass
                        if process.poll() is not None:
                            raise AssertionError(log.read_text())
                        time.sleep(.25)
                    else: raise AssertionError('Server did not start: '+log.read_text())
                    with sync_playwright() as p:
                        launch={}
                        if args.browser=='chromium' and os.environ.get('TRACE_BROWSER_EXECUTABLE'):
                            launch['executable_path']=os.environ['TRACE_BROWSER_EXECUTABLE']
                        browser=getattr(p,args.browser).launch(**launch)
                        page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
                        errors=[]; outbound=[]
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        page.on('request',lambda r:outbound.append(r.url) if not r.url.startswith(url) and not r.url.startswith('blob:') else None)
                        try:
                            page.goto(url)
                            page.locator('#healthNav').click()
                            expect(page.locator('#healthSummary')).to_contain_text('まだ未学習')
                            expect(page.locator('.health-boundary')).to_contain_text('未評価')
                            expect(page.locator('#healthCompare')).to_be_disabled()
                            initial=client.get('/api/status').json()['checkpoint_id']
                            created=[]
                            for i,text in enumerate(['動作確認専用の人工文章です。今日は条件を整理した。',
                                                     '別の人工ノート。昨日の検証結果を記録して次の条件を決める。']):
                                note=client.post('/api/pages',headers=HEADERS,json={'title':f'人工テスト {i+1}','body':text}).json();created.append(note)
                                for repeat in range(2 if i==0 else 1):
                                    j=client.post('/api/feed',headers=HEADERS,json={'page_id':note['id'],'revision':1,'repeats':2,'request_key':f'health-{i}-{repeat}'}).json()
                                    for _ in range(150):
                                        job=next(x for x in client.get('/api/history').json()['jobs'] if x['id']==j['id'])
                                        if job['status']=='succeeded':break
                                        if job['status']=='failed':raise AssertionError(job)
                                        time.sleep(.1)
                                    else:raise AssertionError('Learning job timed out')
                            # Reopen also refreshes the available comparison pages.
                            page.locator('#pagesNav').click();page.locator('#healthNav').click()
                            expect(page.locator('#healthRows tr')).to_have_count(3)
                            expect(page.locator('#healthFirstChart circle')).to_have_count(2)
                            expect(page.locator('#healthRetentionChart circle')).to_have_count(1)
                            expect(page.locator('#healthSummary')).to_contain_text('再投入 1件')
                            page.locator('#healthDays').select_option('30')
                            expect(page.locator('#healthSelection')).to_contain_text('30日間')
                            point=page.locator('#healthFirstChart circle').first
                            point.focus();expect(page.locator('#healthFirstChart .chart-readout')).to_contain_text('初回Loss')
                            # Automatic refresh must not steal focus from an unchanged chart.
                            page.wait_for_timeout(2300)
                            expect(point).to_be_focused()
                            page.locator('#healthRows button').first.click()
                            expect(page.locator('dialog.health-detail')).to_be_visible()
                            page.keyboard.press('Escape')
                            before_jobs=len(client.get('/api/history').json()['jobs'])
                            page.locator('#healthModelA').select_option(initial)
                            page.locator('#healthCompare').click()
                            expect(page.locator('#healthOutputs article')).to_have_count(2,timeout=30000)
                            assert page.locator('#healthOutputs pre').count()==0
                            assert len(client.get('/api/history').json()['jobs'])==before_jobs
                            page.locator('#healthOutputs button').first.click()
                            expect(page.locator('#healthOutputs pre')).to_have_count(1)
                            page.locator('#healthOutputs button').first.click()
                            expect(page.locator('#healthOutputs pre')).to_have_count(0)
                            with page.expect_download() as download:
                                page.locator('#healthExport').click()
                            data=json.loads(Path(download.value.path()).read_text())
                            assert data['summary']['succeeded']==3
                            assert data['quality']['independent_validation']=='unavailable'
                            assert '動作確認専用' not in json.dumps(data,ensure_ascii=False)
                            assert '人工テスト' not in json.dumps(data,ensure_ascii=False)
                            assert client.get('/api/status').json()['checkpoint_id']!=initial
                            # Restore filters descendants out of current-lineage charts, not history.
                            client.post('/api/checkpoints/'+initial+'/restore',headers=HEADERS).raise_for_status()
                            page.locator('#healthRefresh').click()
                            expect(page.locator('#healthRows .table-empty')).to_be_visible()
                            page.locator('#healthScope').select_option('all')
                            expect(page.locator('#healthRows tr')).to_have_count(3)
                            # Persistent offline warning is actionable and stale data is labelled.
                            page.route('**/api/learning-health*',lambda route:route.abort())
                            page.locator('#healthRefresh').click()
                            expect(page.locator('#healthError')).to_be_visible()
                            expect(page.locator('#healthExport')).to_be_disabled()
                            page.unroute('**/api/learning-health*')
                            page.locator('#healthRefresh').click()
                            expect(page.locator('#healthError')).to_be_hidden()
                            for width in (1440,1024):
                                page.set_viewport_size({'width':width,'height':1000})
                                assert page.locator('.app').bounding_box()['width']<=width
                                assert page.locator('#healthView').evaluate('(e)=>e.scrollWidth <= e.clientWidth+1')
                            if args.artifacts:
                                page.set_viewport_size({'width':1440,'height':1000})
                                page.screenshot(path=str(args.artifacts/'learning-health.png'),full_page=True)
                                (args.artifacts/'health-snapshot.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
                            assert not errors,errors
                            assert not outbound,outbound
                        except BaseException:
                            if args.artifacts:
                                page.screenshot(path=str(args.artifacts/'health-failure.png'),full_page=True)
                            raise
                        finally:browser.close()
            finally:
                process.terminate();process.wait(timeout=20)
    print('PASS: health empty/real training/repeat/retention/filters/focus/detail/masked comparison/export/restore/offline/desktop sizes; no outbound requests')


if __name__=='__main__':
    main()
