#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
# Stabil für Demo: ohne --reload (sonst Neustart-Loop durch .venv-Änderungen)
exec uvicorn main:app --host 0.0.0.0 --port 8000
