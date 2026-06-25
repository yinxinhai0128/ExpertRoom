@echo off
cd /d %~dp0
set HTTP_PROXY=
set HTTPS_PROXY=

if not exist .venv (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    echo [setup] Installing dependencies...
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com --only-binary :all:
)

call .venv\Scripts\activate.bat
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
