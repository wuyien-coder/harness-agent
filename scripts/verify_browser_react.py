"""Inspect persisted ReAct trace and citations in desktop/mobile UI."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'reports'

def main():
    evidence=json.loads((REPORTS/'react_validation.json').read_text(encoding='utf-8'))
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script('sessionStorage.setItem("mathknow.session", '+json.dumps(evidence['session_id'])+');')
        page.goto('http://127.0.0.1:8001/web/',wait_until='networkidle')
        page.locator('.agent-trace li').first.wait_for()
        assert page.locator('.agent-trace li').count()==len(evidence['agent_trace'])
        assert page.locator('.citation-card .source-kind').all_text_contents()==['图谱','图谱']
        assert page.locator('.message.assistant .message-text p').count()>0
        page.screenshot(path=str(REPORTS/'react_desktop.png'),full_page=True)
        page.reload(wait_until='networkidle')
        page.locator('.agent-trace li').first.wait_for()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
        page.screenshot(path=str(REPORTS/'react_mobile.png'),full_page=True)
        browser.close()
    assert not errors
    report={'status':'passed','checks':['persisted ReAct tool trace','correct graph citation label',
        'Markdown answer','trace survives page reload','mobile layout'],'console_errors':errors}
    (REPORTS/'browser_react_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
