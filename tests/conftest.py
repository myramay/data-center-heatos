import os

# Tests use the rule-based Jev mock; the live app defaults to OpenJev (ml/jev_openjev.py).
os.environ.setdefault("HEATOS_JEV", "mock")
