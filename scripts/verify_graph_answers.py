"""Check that actual positive relations are answered and empty queries are explicit."""
from pathlib import Path
import json
import httpx

ROOT=Path(__file__).resolve().parents[1]

def main():
    results=[]
    with httpx.Client(base_url='http://127.0.0.1:8001',trust_env=False,timeout=180) as client:
        for question in ['胡和生师从谁？请先查图谱，关系明确就直接回答。',
                         '仅根据图谱查询苏步青自己的老师是谁，查询出向 teacher 关系；如果为空，说明未命中即可，不要补查原文。']:
            response=client.post('/api/v1/chat',json={'question':question})
            response.raise_for_status(); result=response.json()
            assert result['engine']=='react' and result['agent_trace']
            results.append(result)
        positive, empty=results
        assert '苏步青' in positive['answer'] and positive['citations']
        assert any(s['tool']=='graph_neighbors' and s['result_count']>0 for s in positive['agent_trace'])
        assert any(s['tool']=='graph_neighbors' and s['result_count']==0 for s in empty['agent_trace'])
        assert not empty['citations'], 'Empty graph query must not become citation evidence'
        report={'status':'passed','positive':positive,'empty':empty}
        (ROOT/'reports/graph_relation_fix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':'passed','positive_answer':positive['answer'],
            'empty_answer':empty['answer'],'positive_trace':positive['agent_trace'],
            'empty_trace':empty['agent_trace']},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
