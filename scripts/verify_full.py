"""Live acceptance checks against the complete local stack (makes two LLM calls)."""
from pathlib import Path
import json
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8001'
report={'checks':[]}

def main():
    with httpx.Client(base_url=BASE,timeout=180,trust_env=False) as client:
        def get(path):
            response=client.get(path); response.raise_for_status(); return response.json()
        stats=get('/api/v1/stats')
        assert stats['dataset']=='full' and stats['config']['database']=='postgresql'
        assert stats['index']['chunks']==9921 and stats['index']['embedder']=='BgeEmbedding'
        assert stats['llm_provider']=='openai' and stats['config']['queue']=='celery'
        report['initial_stats']=stats
        report['checks'].append('full corpus, BGE, PostgreSQL and Celery configuration')
        done=None; events=[]; tokens=[]; started=time.perf_counter(); first_token=None
        request={'question':'华罗庚在解析数论方面有哪些贡献？请结合资料简要回答。','mode':'rag','engine':'langgraph'}
        with client.stream('POST','/api/v1/chat/stream',json=request) as response:
            response.raise_for_status()
            event=''
            for line in response.iter_lines():
                if line.startswith('event:'): event=line[6:].strip()
                elif line.startswith('data:'):
                    data=json.loads(line[5:].strip()); events.append(event)
                    if event=='error': raise RuntimeError('LLM stream failed: '+str(data))
                    if event=='token':
                        tokens.append(data['text'])
                        if first_token is None: first_token=time.perf_counter()-started
                    if event=='done': done=data
        assert done and done['citations'] and len(done['answer'])>30
        assert events[0]=='retrieval' and len(tokens)>1
        assert done['usage']['prompt_tokens']+done['usage']['completion_tokens']>0
        report['stream']={'events':len(events),'token_events':len(tokens),
            'first_token_seconds':round(first_token,2),'total_seconds':round(time.perf_counter()-started,2),
            'engine':done['engine'],'usage':done['usage'],'citations':len(done['citations']),
            'answer':done['answer']}
        report['checks'].append('real LLM streamed text, citations and usage')
        sid=done['session_id']
        history=get('/api/v1/sessions/'+sid)
        assert len(history['messages'])==2
        response=client.post('/api/v1/chat',json={**request,'session_id':sid})
        response.raise_for_status(); second=response.json()
        report['repeat_timings']=second['timings_ms']
        assert 'cache' in second['timings_ms'], second['timings_ms']
        assert len(get('/api/v1/sessions/'+sid)['messages'])==4
        assert any(s['id']==sid for s in get('/api/v1/sessions')['sessions'])
        report['checks'].append('Redis retrieval cache hit and persisted session history')
        response=client.post('/api/v1/chat',json={'question':'苏步青师从谁？','mode':'agent','engine':'langgraph'})
        response.raise_for_status(); graph=response.json()
        assert graph['engine']=='langgraph' and graph['citations'] and graph['answer']
        report['graph_agent']={'engine':graph['engine'],'answer':graph['answer'],
            'notes':graph['notes'],'citations':len(graph['citations']),'usage':graph['usage']}
        report['checks'].append('actual LangGraph intent routing and graph question')
        text='人物：全量联调探针\n来源：自动验收临时资料\n====\n全量验收蓝桥协议使用紫杉标记确认异步摄取，验收编号为 FULL-20261017。'
        response=client.post('/api/v1/documents/upload',files={'file':('full_acceptance.txt',text.encode('utf-8'),'text/plain')})
        response.raise_for_status(); job=response.json()['job_id']
        for _ in range(120):
            jobs=get('/api/v1/documents/jobs'); state=next(j for j in jobs if j['id']==job)
            if state['status'] in ('done','completed','success'): break
            if state['status']=='failed': raise RuntimeError('Ingestion failed: '+str(state.get('error')))
            time.sleep(1)
        else: raise RuntimeError('Celery job timeout')
        report['ingestion_job']=state
        response=client.post('/api/v1/search',json={'query':'全量验收蓝桥协议使用什么标记？'})
        response.raise_for_status()
        assert any('紫杉标记' in h['text'] for h in response.json()['hits'])
        doc=next(d for d in get('/api/v1/documents/list') if d['person_name']=='全量联调探针')
        response=client.delete('/api/v1/documents/'+str(doc['id'])); response.raise_for_status()
        assert get('/api/v1/stats')['index']['chunks']==9921
        report['checks'].append('actual Celery ingestion, live index refresh, retrieval and removal')
        report['validation_session_id']=sid
        report['status']='passed'
        print(json.dumps({'status':'passed','checks':report['checks'],'stream':{k:v for k,v in report['stream'].items() if k!='answer'}},ensure_ascii=False),flush=True)

if __name__=='__main__':
    try: main()
    except Exception as exc:
        report['status']='failed'; report['error_type']=type(exc).__name__; print(type(exc).__name__+': '+str(exc),flush=True); raise
    finally:
        (ROOT/'reports/full_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
