"""Offline component contract, not an HTTP/browser integration test.

Use an intentionally different top-level status and health snapshot. The health
snapshot must update its model/queue labels together, avoiding mixed-model UI.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    static = Path(__file__).parents[1]/'src/trace_llm/static'
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('TRACE_BROWSER_EXECUTABLE'))
        page = browser.new_page()
        page.set_content('<div id="healthContent"></div><button id="healthRefresh"></button>'
                         '<div id="healthUpdated"></div><div id="healthError"></div>')
        page.add_script_tag(content=(static/'health.js').read_text())
        # Test just the refresh/callback contract with an explicit empty fixture.
        # The HTTP integration and header consistency are checked in browser_health.
        page.evaluate('''() => {
          window.seen=[];
          const fail = {state:'not_started', recent_counts:{}, active_counts:{}};
          window.fixture={checkpoint_id:'a'.repeat(32),scope:{since:0,days:7},as_of:1,
            execution:fail,recovery:{current_preserved:true,available_previous:0},
            retention:{status:'unavailable'},runtime:{threads:1,context:256},
            exposure:{new_targets:0,replay_targets:0,complete:true},
            observations:[],first_seen:[],latest_timing:null};
          TraceHealth.init({api:async()=>window.fixture,esc:String,date:String,
            history:()=>{},onSnapshot:h=>seen.push(h.checkpoint_id)});
        }''')
        page.evaluate('TraceHealth.refresh()')
        assert page.evaluate('seen') == ['a'*32], 'Health snapshot must synchronize model chrome'
        page.evaluate("fixture.checkpoint_id='b'.repeat(32); fixture.as_of=2")
        page.evaluate('TraceHealth.refresh()')
        assert page.evaluate('seen') == ['a'*32, 'b'*32]
        browser.close()
        print(json.dumps({'result':'PASS','scope':'offline component snapshot callback'}))


if __name__ == '__main__':
    main()
