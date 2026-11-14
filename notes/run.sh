#!/bin/bash
cd ~/onset-wt/decide && ~/onset/.venv/bin/python -m pytest -q tests/test_decide.py tests/test_types.py "$@" 2>&1 | tail -40
