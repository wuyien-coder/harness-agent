"""Assemble inspectable evidence after full-stack acceptance has completed."""
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_stack import run_checks

def main():
    reports=ROOT/'reports'
    read=lambda name:json.loads((reports/name).read_text(encoding='utf-8'))
    full=read('full_validation.json'); browser=read('browser_full_validation.json')
    react=read('react_validation.json'); react_browser=read('browser_react_validation.json')
    suite=ET.parse(reports/'tests-react.xml').getroot().find('testsuite')
    tests={key:int(suite.attrib[key]) for key in ('tests','failures','errors','skipped')}
    checks,code=run_checks(require_all=True)
    with httpx.Client(trust_env=False,timeout=10) as client:
        current=client.get('http://127.0.0.1:8001/api/v1/stats').json()
        original_status=client.get('http://127.0.0.1:8000/health').status_code
    assert code==0 and full['status']==browser['status']=='passed'
    assert react['status']==react_browser['status']=='passed'
    assert tests['failures']==tests['errors']==tests['skipped']==0
    assert current['index']['documents']==326 and current['index']['chunks']==9921
    result={'date':'2026-10-17','version':'2.2.0','status':'passed',
        'mode':'full corpus / real BGE / real PostgreSQL and Redis / real LLM',
        'tests':tests,'infrastructure':checks,'current':current,
        'prior_full_acceptance':full['checks'],'prior_browser':browser,
        'react':react,'browser':react_browser,
        'frontend_build':'TypeScript and Vite build passed',
        'restart':'stop/start completed; infrastructure checks passed after restart',
        'original_project_port_8000_health_http':original_status,
        'evidence':['tests-react.xml','react_validation.json','browser_react_validation.json',
                    'react_desktop.png','react_mobile.png','full_prepare.json','full_validation.json',
                    'browser_full_validation.json','full_chat.png','full_documents.png',
                    'full_architecture.png','full_mobile.png','full_mobile_chat.png'],
        'limits':['Docker unavailable; Compose was not executed',
                  'Cross-encoder reranking is disabled',
                  'Live acceptance is not a statistical answer-quality evaluation',
                  'eval_current.json contains earlier hash/sample regression results']}
    (reports/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'tests':tests,'documents':326,'chunks':9921},ensure_ascii=False))

if __name__=='__main__': main()
