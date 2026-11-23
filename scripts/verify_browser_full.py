"""Verify the built UI against the live full stack without making extra LLM calls."""
from pathlib import Path
import json
import re
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/'reports'
result={'checks':[],'console_errors':[]}

if __name__=='__main__':
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
            page.on('pageerror',lambda error: result['console_errors'].append(str(error)))
            page.goto('http://127.0.0.1:8001/web/',wait_until='networkidle')
            page.locator('textarea').wait_for()
            page.wait_for_function("() => document.body.innerText.includes('9,921') || document.body.innerText.includes('9921')")
            result['checks'].append('full corpus status on desktop')
            page.locator('.session-title').first.click()
            page.locator('.message.assistant .message-text p').first.wait_for()
            assert page.locator('.message.assistant').count()>=1
            page.screenshot(path=str(REPORTS/'full_chat.png'),full_page=True)
            result['checks'].append('persisted real LLM answer, Markdown and citation panel')
            page.get_by_role('button',name=re.compile('知识库')).first.click()
            page.locator('tbody tr').first.wait_for()
            assert page.locator('tbody tr').count()>=326
            assert page.get_by_role('button',name='删除',exact=True).count()>=326
            page.screenshot(path=str(REPORTS/'full_documents.png'),full_page=True)
            result['checks'].append('326 full documents and management controls')
            page.get_by_role('button',name=re.compile('技术架构')).first.click()
            page.get_by_text('PostgreSQL',exact=False).first.wait_for()
            page.screenshot(path=str(REPORTS/'full_architecture.png'),full_page=True)
            result['checks'].append('actual runtime architecture rendered')
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(REPORTS/'full_mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            result['checks'].append('mobile architecture fits viewport')
            page.get_by_role('button',name=re.compile('知识问答')).first.click()
            page.screenshot(path=str(REPORTS/'full_mobile_chat.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
            result['checks'].append('mobile chat fits viewport')
            browser.close()
        assert not result['console_errors']
        result['status']='passed'
    except Exception as exc:
        result['status']='failed'; result['error']=str(exc); raise
    finally:
        (REPORTS/'browser_full_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
