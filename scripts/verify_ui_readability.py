"""Browser regression for long history, long tool arguments and readable typography."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'reports'

def main():
    errors=[]; checks=[]
    # Existing long session reproduces sidebar overlap without any new model calls.
    session_id='sess-a6c540732eff412fb47bd91078026c67'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        for width,height in [(1440,900),(1366,768),(1100,700),(1024,768),(855,700),(390,844)]:
            page=browser.new_page(viewport={'width':width,'height':height})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script('sessionStorage.setItem("mathknow.session",'+json.dumps(session_id)+');')
            page.goto('http://127.0.0.1:8001/web/',wait_until='networkidle')
            page.locator('.trace-arguments').first.wait_for()
            metrics=page.evaluate('''() => {
              const rect = s => document.querySelector(s).getBoundingClientRect();
              const history=rect('.session-history'), settings=rect('.settings-button');
              const trace=rect('.agent-trace'), reference=rect('.evidence-meta');
              return {horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
                pageHeight:document.documentElement.scrollHeight,
                historyBottom:history.bottom,settingsTop:settings.top,
                traceBottom:trace.bottom,referenceTop:reference.top,
                answerFont:parseFloat(getComputedStyle(document.querySelector('.message.assistant .message-text')).fontSize),
                argumentsFont:parseFloat(getComputedStyle(document.querySelector('.trace-arguments')).fontSize)};
            }''')
            assert not metrics['horizontalOverflow'],(width,metrics)
            assert metrics['answerFont']>=15 and metrics['argumentsFont']>=12
            assert metrics['traceBottom']<=metrics['referenceTop']+1,(width,'trace overlaps references',metrics)
            if width>1050:
                assert metrics['historyBottom']<=metrics['settingsTop']+1,(width,'history overlaps footer',metrics)
                assert metrics['pageHeight']<=height+1,(width,'desktop panels escape viewport',metrics)
            if width in (1366,390):
                page.screenshot(path=str(REPORTS/f'ui_readability_{width}.png'),full_page=True)
            checks.append({'viewport':[width,height],'status':'passed','metrics':metrics})
            page.close()
        page=browser.new_page(viewport={'width':390,'height':844})
        page.goto('http://127.0.0.1:8001/web/',wait_until='networkidle')
        assert page.locator('.prompt-grid button').count()==3
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(REPORTS/'ui_welcome_mobile.png'),full_page=True)
        page.close(); browser.close()
    assert not errors,errors
    report={'status':'passed','checks':checks,'mobile_welcome':'passed','console_errors':errors}
    (REPORTS/'ui_readability.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','viewports':len(checks),'console_errors':errors},ensure_ascii=False))

if __name__=='__main__': main()
