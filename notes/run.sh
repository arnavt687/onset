# Run piece 2 tests inside Ubuntu, hiding MediaPipe's log noise.
cd ~/onset-wt/arms && ~/onset/.venv/bin/python -m pytest -q tests/test_arms.py -p no:cacheprovider "$@" 2>&1 | grep -v -E '^(W0000|I0000|INFO:|WARNING: Logging|WARNING: .model_load)' | tail -60
