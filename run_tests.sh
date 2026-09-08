#!/bin/bash
# Run stabilization tests with SSL workaround for uv Python on Windows
# Usage: bash run_tests.sh [test_file_or_dir...]

export SSL_CERT_FILE=""
export WORED_TEST_DATABASE_URL="postgresql://bot:bot12345@127.0.0.1:5432/wored_qa"
PYTHON="D:/WORED/TASOCHKI/HERMES-WORED/.venv/Scripts/python.exe"
STAGING="D:/WORED_STAGING_20260908"
cd "$STAGING"

if [ $# -eq 0 ]; then
    echo "Running stabilization test suite..."
    "$PYTHON" -m pytest -q -p no:cacheprovider tests/stabilization/
else
    "$PYTHON" -m pytest -q -p no:cacheprovider "$@"
fi