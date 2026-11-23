"""Manage project-owned portable PostgreSQL and Redis without system services."""
from pathlib import Path
import json
import os
import secrets
import subprocess
import sys
import time
import shutil
import hashlib
import tempfile
import psutil
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
STATE = RUNTIME / 'state/infrastructure.json'
PRIVATE = RUNTIME / 'private.env'
# PostgreSQL Windows builds use ANSI paths during initdb; choose an ASCII path.
LOCATION = RUNTIME / 'state/portable-location.json'
if LOCATION.exists():
    PORTABLE = Path(json.loads(LOCATION.read_text(encoding='utf-8'))['directory'])
else:
    suffix = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    PORTABLE = Path(tempfile.gettempdir()) / ('mathknow-runtime-' + suffix)

def credentials():
    values = dict(dotenv_values(PRIVATE)) if PRIVATE.exists() else {}
    if not values.get('POSTGRES_PASSWORD'):
        values['POSTGRES_PASSWORD'] = secrets.token_hex(24)
    if not values.get('MATHKB_LLM__API_KEY'):
        old = dotenv_values(ROOT.parent / '简历项目_知识问答平台/.env')
        values['MATHKB_LLM__API_KEY'] = old.get('MATHKB_LLM__API_KEY', '')
    PRIVATE.write_text(''.join(f'{k}={v}\n' for k,v in values.items()), encoding='utf-8')
    return values

def configure(password):
    old = dotenv_values(ROOT.parent / '简历项目_知识问答平台/.env')
    values = dict(dotenv_values(ROOT / '.env.example'))
    values.update({
        'MATHKB_DATABASE_URL': f'postgresql+psycopg://mathkb:{password}@127.0.0.1:15432/mathkb',
        'MATHKB_CREATE_SCHEMA': 'false', 'MATHKB_DATA_DIR': str(ROOT/'data/full'),
        'MATHKB_BIOGRAPHIES_DIR': str(ROOT/'data/full/biographies'),
        'MATHKB_INDEX_DIR': str(ROOT/'.index/full'),
        'MATHKB_KEYWORD__PERSIST_DIR': str(ROOT/'.index/full/bm25'),
        'MATHKB_LLM__PROVIDER': 'openai', 'MATHKB_LLM__API_KEY': '',
        'MATHKB_LLM_CONFIG_FILE': str(PRIVATE),
        'MATHKB_LLM__BASE_URL': old.get('MATHKB_LLM__BASE_URL','https://api.deepseek.com/v1'),
        'MATHKB_LLM__MODEL': old.get('MATHKB_LLM__MODEL','deepseek-chat'),
        'MATHKB_LLM__TIMEOUT': '120',
        'MATHKB_EMBEDDING__PROVIDER': 'bge',
        'MATHKB_EMBEDDING__CACHE_DIR': old['MATHKB_EMBEDDING__CACHE_DIR'],
        'MATHKB_EMBEDDING__VECTOR_CACHE_DIR': str(ROOT/'.index/embedding-cache'),
        'MATHKB_VECTOR__PROVIDER': 'pgvector', 'MATHKB_VECTOR__COLLECTION': 'mathkb_full',
        'MATHKB_QUEUE__PROVIDER': 'celery', 'MATHKB_QUEUE__REDIS_URL': 'redis://127.0.0.1:16379/0',
        'MATHKB_CACHE__ENABLED': 'true', 'MATHKB_CACHE__REDIS_URL': 'redis://127.0.0.1:16379/1',
        'MATHKB_SERVER__PORT': '8001',
        'MATHKB_SERVER__CORS_ORIGINS': '["http://127.0.0.1:8001","http://localhost:8001","http://localhost:5173"]',
        'MODEL_CACHE_DIR': old['MATHKB_EMBEDDING__CACHE_DIR'],
    })
    (ROOT/'.env').write_text(''.join(f'{k}={v or ""}\n' for k,v in values.items()),encoding='utf-8')

def start():
    private = credentials()
    if not (ROOT/'.env').exists():
        configure(private['POSTGRES_PASSWORD'])
    logs = RUNTIME/'logs'
    logs.mkdir(parents=True,exist_ok=True)
    PORTABLE.mkdir(parents=True, exist_ok=True)
    if not (PORTABLE/'postgres/Library/bin/postgres.exe').exists():
        shutil.copytree(RUNTIME/'postgres', PORTABLE/'postgres', dirs_exist_ok=True)
    pg_bin = PORTABLE/'postgres/Library/bin'
    env = dict(os.environ, PATH=str(pg_bin)+os.pathsep+os.environ.get('PATH',''), PYTHONUTF8='1')
    pgdata = PORTABLE/'pgdata'
    pgdata.mkdir(exist_ok=True)
    if not (pgdata/'PG_VERSION').exists():
        pwfile=PORTABLE/'init-password'
        pwfile.write_text(private['POSTGRES_PASSWORD'],encoding='ascii')
        try:
            subprocess.run([str(pg_bin/'initdb.exe'),'-D',str(pgdata),'-U','mathkb',
                '--encoding=UTF8','--locale=C','--auth-host=scram-sha-256',
                '--auth-local=trust','--pwfile',str(pwfile)],env=env,check=True)
        finally:
            pwfile.unlink(missing_ok=True)
    status=subprocess.run([str(pg_bin/'pg_ctl.exe'),'-D',str(pgdata),'status'],env=env,capture_output=True)
    if status.returncode:
        subprocess.run([str(pg_bin/'pg_ctl.exe'),'-D',str(pgdata),'-l',str(PORTABLE/'postgres.log'),
            '-o','-h 127.0.0.1 -p 15432','-w','start'],env=env,check=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    import psycopg
    from psycopg import sql
    with psycopg.connect(host='127.0.0.1',port=15432,user='mathkb',password=private['POSTGRES_PASSWORD'],dbname='postgres',autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='mathkb'").fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier('mathkb')))
    with psycopg.connect(host='127.0.0.1',port=15432,user='mathkb',password=private['POSTGRES_PASSWORD'],dbname='mathkb',autocommit=True) as conn:
        conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
        print('PostgreSQL / pgvector ready',flush=True)
    from redis import Redis
    client=Redis(host='127.0.0.1',port=16379,socket_connect_timeout=1)
    try:
        ready=client.ping()
    except Exception:
        ready=False
    state={}
    if STATE.exists():
        state=json.loads(STATE.read_text(encoding='utf-8'))
    if not ready:
        if not (PORTABLE/'redis/redis-server.exe').exists():
            source = next((RUNTIME/'redis').rglob('redis-server.exe')).parent
            shutil.copytree(source, PORTABLE/'redis', dirs_exist_ok=True)
        redisdata=PORTABLE/'redis-data'
        redisdata.mkdir(exist_ok=True)
        with (logs/'redis.log').open('a',encoding='utf-8') as output:
            process=subprocess.Popen([str(PORTABLE/'redis/redis-server.exe'),'--bind','127.0.0.1',
                '--port','16379','--appendonly','yes','--dir','.'],cwd=redisdata,
                stdout=output,stderr=output,creationflags=subprocess.CREATE_NO_WINDOW)
        state['redis']={'pid':process.pid,'created':psutil.Process(process.pid).create_time()}
        for _ in range(30):
            try:
                if client.ping(): break
            except Exception: pass
            time.sleep(1)
        else: raise RuntimeError('Redis startup failed; see .runtime/logs/redis.log')
    client.close()
    STATE.write_text(json.dumps(state),encoding='utf-8')
    print('Redis ready; complete configuration prepared',flush=True)

def stop():
    pg_bin=PORTABLE/'postgres/Library/bin'
    subprocess.run([str(pg_bin/'pg_ctl.exe'),'-D',str(PORTABLE/'pgdata'),'-m','fast','stop'],
        creationflags=subprocess.CREATE_NO_WINDOW,check=False)
    if STATE.exists():
        state=json.loads(STATE.read_text(encoding='utf-8'))
        record=state.get('redis',{})
        try:
            process=psutil.Process(record['pid'])
            if abs(process.create_time()-record['created'])<1 and Path(process.exe()).resolve()==(PORTABLE/'redis/redis-server.exe').resolve():
                from redis import Redis
                Redis(host='127.0.0.1',port=16379).shutdown()
        except (psutil.Error,KeyError): pass
    print('Project infrastructure stopped')

if __name__=='__main__':
    if sys.argv[1]=='configure': configure(credentials()['POSTGRES_PASSWORD'])
    elif sys.argv[1]=='start': start()
    elif sys.argv[1]=='stop': stop()
