#!/bin/zsh
cd -- "${0:A:h}"
exec .venv/bin/python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 8000
