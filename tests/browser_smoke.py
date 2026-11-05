"""Optional real-browser smoke test. Uses a disposable data directory, never real notes.
Run: python tests/browser_smoke.py [--screenshot /path/to/screen.png]
Requires: playwright install chromium
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='trace-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
        process = subprocess.Popen([sys.executable, '-m', 'trace_llm.app', '--data-dir', directory,
                                    '--port', str(port), '--threads', '1'], env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(120):
                try:
                    if httpx.get(url+'/api/healthz').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                raise AssertionError('Server did not start')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('TRACE_BROWSER_EXECUTABLE'))
                page = browser.new_page(viewport={'width':1360,'height':900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.locator('#firstPage').click()
                page.locator('#body').fill('これは動作確認用の人工文章です。\n自分の記録を書くページを検証します。')
                page.locator('#title').fill('動作確認ノート')
                page.locator('#save').click()
                expect(page.locator('#saveStatus')).to_have_text('保存済み')
                initial = httpx.get(url+'/api/status').json()['checkpoint_id']
                assert httpx.get(url+'/api/history').json()['jobs'] == []
                page.locator('#respond').click()
                page.locator('#reveal').wait_for()
                page.locator('#reveal').click()
                assert page.locator('.response-text').count() == 1
                page.locator('#maskToggle').click()
                assert page.locator('#reveal').count() == 1
                for direction in (1, -1):
                    # Wait until the UI action lock is released before each swipe.
                    expect(page.locator('#respond')).to_be_enabled()
                    box = page.locator('.page-row.active').bounding_box()
                    x, y = box['x'] + box['width']/2, box['y'] + box['height']/2
                    page.mouse.move(x,y);page.mouse.down()
                    page.mouse.move(x + 90*direction,y,steps=12);page.mouse.up()
                    expected=1 if direction==1 else 2
                    for _ in range(100):
                        history=httpx.get(url+'/api/history').json()
                        if len(history['jobs'])==expected and history['jobs'][0]['status']=='succeeded':
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError(f'Swipe not trained: {history}')
                expect(page.locator('#respond')).to_be_enabled()
                page.locator('#historyNav').click()
                page.locator(f'[data-restore="{initial}"]').wait_for()
                page.on('dialog',lambda dialog:dialog.accept())
                page.locator(f'[data-restore="{initial}"]').click()
                expect(page.locator('#respond')).to_be_enabled()
                for _ in range(50):
                    history=httpx.get(url+'/api/history').json()
                    if history['checkpoints'][0]['kind']=='restore':break
                    time.sleep(0.1)
                assert history['checkpoints'][0]['parent_id']==initial
                page.locator('#pagesNav').click()
                assert page.locator('#body').input_value().startswith('これは動作確認用')
                if args.screenshot:
                    page.screenshot(path=args.screenshot,full_page=True)
                page.reload();page.locator('#body').wait_for()
                assert page.locator('#title').input_value()=='動作確認ノート'
                mobile=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
                mobile.goto(url);mobile.locator('#body').wait_for()
                assert mobile.locator('.app').bounding_box()['width'] <= 390
                assert not errors, errors
                browser.close()
            print('PASS: create/edit, masked real generation, left/right training, restore, reload, mobile layout')
        finally:
            process.terminate()
            process.wait(timeout=20)


if __name__ == '__main__':
    main()
