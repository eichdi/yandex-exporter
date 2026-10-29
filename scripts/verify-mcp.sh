#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${MCP_PYTHON:-"$project_dir/.venv/bin/python"}
verification_dir=$(mktemp -d "${TMPDIR:-/tmp}/yandex-mcp-check.XXXXXX")
trap 'rm -rf "$verification_dir"' EXIT HUP INT TERM
cd "$project_dir"

export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_CACHE_DIR=1
export COVERAGE_FILE="$verification_dir/coverage"
export PYTHONPYCACHEPREFIX="$verification_dir/pycache"

"$python_bin" -m coverage run --branch --source=yandex_mcp,mcp_server -m unittest discover -v
"$python_bin" -m coverage report --fail-under=80 -m
"$python_bin" -m compileall -q export_tracker.py export_wiki.py yandex_mcp.py mcp_server.py tests it
"$python_bin" -m pip check
