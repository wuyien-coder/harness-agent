[CmdletBinding()]
param([int]$Port = 8001)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot

try {
    $pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw '未找到 Python。请先安装 Python 3.11 或更高版本，并安装 requirements.txt 中的依赖。'
        }
        $pythonPath = $pythonCommand.Source
    }

    & $pythonPath -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'; import uvicorn, fastapi, sqlalchemy, pydantic_settings, numpy, rank_bm25, jieba"
    if ($LASTEXITCODE -ne 0) {
        throw '缺少运行依赖。请在项目目录执行：python -m pip install -r requirements.txt'
    }
    $frontendIndex = Join-Path $projectRoot 'frontend\dist\index.html'
    if (-not (Test-Path -LiteralPath $frontendIndex)) {
        throw '尚未构建前端。请先进入 frontend，执行 npm ci 和 npm run build。'
    }

    # 仅覆盖此启动进程的环境变量；不修改 .env 或系统设置。
    # 始终强制离线实现，避免继承其他项目配置后消费付费 API。
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $env:HF_HUB_OFFLINE = '1'
    $env:MATHKB_ENV = 'dev'
    $env:MATHKB_CREATE_SCHEMA = 'true'
    $env:MATHKB_DATABASE_URL = 'sqlite:///./.index/demo.db'
    $env:MATHKB_DATA_DIR = Join-Path $projectRoot 'data\seed'
    $env:MATHKB_BIOGRAPHIES_DIR = Join-Path $projectRoot 'data\seed\biographies'
    $env:MATHKB_INDEX_DIR = Join-Path $projectRoot '.index\demo'
    $env:MATHKB_KEYWORD__PERSIST_DIR = Join-Path $projectRoot '.index\demo\bm25'
    $env:MATHKB_VECTOR__PROVIDER = 'memory'
    $env:MATHKB_EMBEDDING__PROVIDER = 'hash'
    $env:MATHKB_RERANK__ENABLED = 'false'
    $env:MATHKB_QUEUE__PROVIDER = 'local'
    $env:MATHKB_CACHE__ENABLED = 'false'
    $env:MATHKB_LLM__PROVIDER = 'stub'
    $env:MATHKB_LLM = '{"provider":"stub","api_key":""}'
    $env:MATHKB_LLM__API_KEY = ''
    $env:MATHKB_SERVER__API_TOKEN = ''
    $env:MATHKB_SERVER = '{"api_token":""}'
    $env:MATHKB_SERVER__CORS_ORIGINS = "[`"http://127.0.0.1:$Port`",`"http://localhost:$Port`"]"

    Write-Host ''
    Write-Host '学位论文知识问答：离线演示已启动，首次构建索引需要稍等片刻。'
    Write-Host "浏览器打开 http://127.0.0.1:$Port/ ，按 Ctrl+C 停止服务。"
    Write-Host '当前使用 SQLite / 内存索引 / 本地任务 / 离线答案，不会调用收费模型。'
    & $pythonPath -m uvicorn mathkb.main:app --app-dir src --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "服务退出，错误码 $LASTEXITCODE。请检查端口是否已被占用。"
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
