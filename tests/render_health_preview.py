"""Offline component rendering when local browser navigation is administratively blocked.

This is NOT the HTTP/CSP end-to-end test (browser_health.py is). It renders the exact
checked-in DOM/CSS/JS via set_content, with fetch bridged to the real FastAPI TestClient.
Synthetic notes in a temporary store only. No enterprise browser policy is modified.
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
import torch

from trace_llm.app import create_app, STATIC


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(1)
    with tempfile.TemporaryDirectory(prefix='trace-health-render-') as directory:
        with TestClient(create_app(directory,worker=False),base_url='http://127.0.0.1') as client:
            e=client.app.state.engine
            fixtures=[
                ('実験用：小さく試す','これは動作確認用の人工日記です。条件をひとつ変えて結果を確認した。'),
                ('実験用：記録を残す','今日は検証の手順を記録した。結果だけでなく、試した理由も文章にする。'),
                ('実験用：文章の続き','昨日の記録を読み返した。同じ条件で比べると、変化を少しずつ確認できる。'),
                ('実験用：考えを整理','考えていることを短い文章にまとめた。まず小さく確かめて、その結果を残す。'),
                ('実験用：振り返り','学習の記録から、処理が完了したことと、予測が改善したことを区別した。'),
                ('実験用：次の検証','これは人工文章です。次の検証では、まだ学習していない文章も比べる。'),
            ]
            for i,(title,text) in enumerate(fixtures):
                note=e.create_page(title,text);e.feed(note['id'],1,2,f'fixture-{i}');e.train_next()
                if i==2:e.feed(note['id'],1,2,'explicit-repeat');e.train_next()
            def bridge(source,url,options):
                if not url.startswith('/api/'):
                    raise AssertionError('Unexpected request '+url)
                response=client.request(options.get('method','GET'),url,
                    headers=options.get('headers',{}),content=options.get('body'))
                return {'status':response.status_code,'text':response.text}
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('TRACE_BROWSER_EXECUTABLE','/usr/bin/chromium'))
                page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                html=(STATIC/'index.html').read_text(encoding='utf-8')
                html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*>', '', html)
                html=re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>', '', html)
                page.set_content(html)
                page.add_style_tag(content=(STATIC/'style.css').read_text(encoding='utf-8')+'\n'+(STATIC/'health.css').read_text(encoding='utf-8'))
                page.expose_binding('__traceHttp',bridge)
                page.evaluate('''() => { window.fetch = async (url, options={}) => {
                  const r = await window.__traceHttp(String(url),options);
                  return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>JSON.parse(r.text)};
                }; }''')
                page.add_script_tag(content=(STATIC/'health.js').read_text(encoding='utf-8'))
                page.add_script_tag(content=(STATIC/'ui_machine.js').read_text(encoding='utf-8'))
                page.add_script_tag(content=(STATIC/'app.js').read_text(encoding='utf-8'))
                expect(page.locator('#respond')).to_be_enabled()
                page.locator('#healthNav').click()
                expect(page.locator('#healthRows tr')).to_have_count(7)
                expect(page.locator('#healthFirstChart circle')).to_have_count(6)
                point=page.locator('#healthFirstChart circle').first;point.focus()
                page.wait_for_timeout(2300);expect(point).to_be_focused()
                page.locator('#healthDays').select_option('30')
                expect(page.locator('#healthSelection')).to_contain_text('30日間')
                page.locator('#healthRefresh').focus()
                page.screenshot(path=str(args.out/'learning-health-desktop.png'),full_page=True)
                page.locator('#healthRows button').first.click()
                expect(page.locator('dialog')).to_be_visible();page.keyboard.press('Escape')
                page.locator('#healthCompare').click()
                expect(page.locator('#healthOutputs article')).to_have_count(2,timeout=30000)
                assert page.locator('#healthOutputs pre').count()==0
                page.locator('#healthOutputs button').first.click()
                expect(page.locator('#healthOutputs pre')).to_have_count(1)
                page.locator('#healthOutputs button').first.click()
                for width in (1024,1440):
                    page.set_viewport_size({'width':width,'height':1000})
                    assert page.locator('#healthView').evaluate('(e)=>e.scrollWidth <= e.clientWidth+1')
                page.locator('#healthCompare').scroll_into_view_if_needed()
                page.screenshot(path=str(args.out/'learning-health-comparison.png'),full_page=True)
                assert not errors,errors
                (args.out/'render-results.json').write_text(json.dumps({'mode':'offline-component-with-real-TestClient',
                    'not_http_e2e':True,'notes':'synthetic-only','errors':errors,'checks':['filter','focus-preserved',
                    'charts','detail','real-generation-comparison','mask-default','1024/1440-no-overflow']},indent=2))
                browser.close()
    print('PASS: offline component render with real API bridge; not a CSP/HTTP end-to-end test')


if __name__=='__main__':main()
