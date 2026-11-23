"""Live ReAct acceptance; uses the configured model and existing local corpus."""
from pathlib import Path
import json
import httpx

ROOT = Path(__file__).resolve().parents[1]

def main():
    with httpx.Client(base_url='http://127.0.0.1:8001',trust_env=False,timeout=180) as client:
        done=None; event=''; tokens=0; events=[]
        question='分别查询华罗庚和陈省身的人物资料，再比较两人的主要研究领域。'
        with client.stream('POST','/api/v1/chat/stream',json={'question':question}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith('event:'): event=line[6:].strip()
                elif line.startswith('data:'):
                    payload=json.loads(line[5:].strip()); events.append(event)
                    if event=='error': raise RuntimeError('ReAct SSE failed')
                    if event=='token': tokens+=1
                    if event=='done': done=payload
        assert done and done['engine']=='react' and done['citations'] and tokens>1
        trace=done['agent_trace']
        assert 2<=len(trace)<=4 and all(s['status']=='ok' for s in trace)
        assert done['usage']['calls']>=3 and done['usage']['completion_tokens']>0
        detail=client.get('/api/v1/sessions/'+done['session_id']).json()
        assert detail['messages'][-1]['agent_metadata']['agent_trace']==trace
        response=client.post('/api/v1/chat',json={
            'question':'请查苏步青自己的老师是谁：先查出向师从边，若无记录，再到他的传记中查找。不要把学生当成老师。'})
        response.raise_for_status(); adaptive=response.json()
        assert adaptive['engine']=='react'
        tools=[step['tool'] for step in adaptive['agent_trace']]
        assert 'graph_neighbors' in tools and 'text_search' in tools, tools
        assert adaptive['citations']
        report={'status':'passed','question':question,'answer':done['answer'],
            'session_id':done['session_id'],'engine':done['engine'],'agent_trace':trace,
            'stop':done['agent_stop'],'usage':done['usage'],'token_events':tokens,
            'events_order_valid':events[0]=='retrieval' and events[-1]=='done',
            'citations':len(done['citations']),'history_trace_persisted':True,
            'adaptive_graph_to_text':{'status':'passed','trace':adaptive['agent_trace'],
                'stop':adaptive['agent_stop'],'answer':adaptive['answer'],'usage':adaptive['usage']}}
        (ROOT/'reports/react_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('answer','session_id')},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
