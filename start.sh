#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=python3.13
$PYTHON -c "import customtkinter" 2>/dev/null || $PYTHON -m pip install customtkinter --break-system-packages -q

$PYTHON monitor.py &
