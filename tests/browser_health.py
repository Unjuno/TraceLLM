"""Disposable desktop acceptance test; screenshots contain only artificial text."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', choices=('chromium', 'firefox', 'webkit'), default='chromium')
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='trace-health-browser-') as temp:
        root = Path(temp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'),
                           OMP_NUM_THREADS='1')
        with (root/'server.txt').open('w') as output:
            process = subprocess.Popen([sys.executable, '-m', 'trace_llm.app', '--data-dir',
                str(root/'private'), '--port', str(port), '--threads', '1'], env=environment,
                stdout=output, stderr=output)
            try:
                for _ in range(120):
                    try:
                        if httpx.get(url+'/api/healthz', trust_env=False).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if process.poll() is not None:
                        raise AssertionError((root/'server.txt').read_text())
                    time.sleep(.25)
                else:
                    raise AssertionError('Server did not become ready')
                with sync_playwright() as p:
                    kind = getattr(p, args.browser)
                    options = {}
                    if args.browser == 'chromium' and os.environ.get('TRACE_BROWSER_EXECUTABLE'):
                        options['executable_path'] = os.environ['TRACE_BROWSER_EXECUTABLE']
                    browser = kind.launch(**options)
                    page = browser.new_page(viewport={'width':1440,'height':1050})
                    errors, remote = [], []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('request', lambda r: remote.append(r.url) if
                            urlsplit(r.url).hostname not in ('127.0.0.1', 'localhost') else None)
                    page.goto(url)
                    expect(page.locator('#newPage')).to_be_enabled()
                    page.locator('#healthNav').click(timeout=3000)
                    expect(page.locator('#healthContent')).to_contain_text('まだ学習していません')
                    expect(page.locator('#healthContent')).to_contain_text('未評価')
                    initial = httpx.get(url+'/api/status', trust_env=False).json()['checkpoint_id']
                    assert httpx.get(url+'/api/history', trust_env=False).json()['jobs'] == []
                    page.locator('#pagesNav').click()
                    page.locator('#firstPage').click()
                    page.locator('#title').fill('人工テストノート')
                    page.locator('#body').fill('今日は検証用の文章を書きました。\n記録と学習の違いを確認します。')
                    page.locator('#save').click()
                    expect(page.locator('#saveStatus')).to_have_text('保存済み')
                    page.locator('#respond').click()
                    expect(page.locator('#reveal')).to_be_visible()
                    expect(page.locator('.response-text')).to_have_count(0)
                    expect(page.locator('#respond')).to_be_enabled()
                    # Use the existing keyboard alternative to swipe.
                    page.locator('.page-row.active').focus()
                    page.keyboard.press('ArrowRight')
                    for _ in range(100):
                        h = httpx.get(url+'/api/history', trust_env=False).json()
                        if h['jobs'] and h['jobs'][0]['status'] == 'succeeded':
                            break
                        time.sleep(.1)
                    else:
                        raise AssertionError(h)
                    expect(page.locator('#respond')).to_be_enabled()
                    page.locator('#healthNav').click()
                    expect(page.locator('#healthContent')).to_contain_text('初回投入前の予測')
                    expect(page.locator('[data-testid="health-observation"]')).to_have_count(1)
                    assert 'まだ学習していません' not in page.locator('#healthContent').inner_text()
                    expect(page.locator('[data-testid="generalization"]')).to_contain_text('未評価')
                    # Training and generation never happen just by reading/refreshing health.
                    public = httpx.get(url+'/api/status', trust_env=False).json()['checkpoint_id']
                    page.locator('#healthRefresh').click()
                    expect(page.locator('#healthRefresh')).to_be_enabled()
                    assert httpx.get(url+'/api/status', trust_env=False).json()['checkpoint_id'] == public
                    assert page.locator('#modelVersion').inner_text() == 'Model '+public[:8]
                    assert '学習待ち 0件' in page.locator('#queueStatus').inner_text()
                    if args.screenshot:
                        page.locator('#toast').wait_for(state='hidden', timeout=7000)
                        page.screenshot(path=args.screenshot, full_page=True)
                    # Notebooks stay masked; navigation preserves the editor's content.
                    page.locator('#pagesNav').click()
                    expect(page.locator('#body')).to_have_value('今日は検証用の文章を書きました。\n記録と学習の違いを確認します。')
                    expect(page.locator('.response-text')).to_have_count(0)
                    # Restore through existing History, then inspect ancestry isolation.
                    page.locator('#historyNav').click()
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.locator(f'[data-restore="{initial}"]').click()
                    expect(page.locator('#respond')).to_be_enabled()
                    page.locator('#healthNav').click()
                    expect(page.locator('[data-testid="health-observation"]')).to_have_count(0)
                    expect(page.locator('#healthContent')).to_contain_text('まだ学習していません')
                    # A disconnected view must disclose stale observations, not look freshly healthy.
                    page.route('**/api/learning-health', lambda route: route.abort())
                    page.locator('#healthRefresh').click()
                    expect(page.locator('#healthError')).to_be_visible()
                    expect(page.locator('#healthError')).to_contain_text('取得できません')
                    # Desktop resize and keyboard navigation work without horizontal overflow.
                    page.set_viewport_size({'width':1024,'height':768})
                    assert page.locator('.app').bounding_box()['width'] <= 1024
                    assert not errors, errors
                    assert not remote, remote
                    browser.close()
                print(json.dumps({'result':'PASS','browser':args.browser,
                    'checks':['empty','real-training','masked-output','read-only-refresh',
                              'restore-ancestry','disconnect','desktop-layout','no-external-requests']}))
            finally:
                process.terminate(); process.wait(timeout=20)


if __name__ == '__main__':
    main()
