"""Fetch the exact, hash-verified conda-forge packages selected by micromamba."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import time
import tarfile
import zipfile
import zstandard

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path('C:/Users/Administrator/.codex/visualizations/2026/10/16/01a14363-b73e-70f3-91d0-288eb2090d9f')

def fetch(item):
    target = STAGING / 'conda-channel' / item['subdir'] / item['fn']
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
        url = 'https://conda.anaconda.org/conda-forge/' + item['subdir'] + '/' + item['fn']
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    data = response.read()
                break
            except Exception:
                if attempt == 4: raise
                time.sleep(2)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise RuntimeError('Package checksum mismatch: ' + item['name'])
        target.write_bytes(data)
    return {'name': item['name'], 'version': item['version'], 'bytes': target.stat().st_size}

if __name__ == '__main__':
    plan = json.loads((ROOT / '.runtime/state/postgres-plan.json').read_text(encoding='utf-8-sig'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch, plan['actions']['FETCH']):
            print(json.dumps(result), flush=True)
    destination = ROOT / '.runtime/postgres'
    destination.mkdir(parents=True, exist_ok=True)
    for item in plan['actions']['FETCH']:
        archive = STAGING / 'conda-channel' / item['subdir'] / item['fn']
        with zipfile.ZipFile(archive) as package:
            member = next(n for n in package.namelist() if n.startswith('pkg-') and n.endswith('.tar.zst'))
            with package.open(member) as compressed:
                with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
                    with tarfile.open(fileobj=reader, mode='r|') as files:
                        files.extractall(destination, filter='data')
    print('Portable PostgreSQL and pgvector extracted', flush=True)
