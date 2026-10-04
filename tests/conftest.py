import os

# Tests use the rule-based Jev mock; the live app defaults to OpenJev (ml/jev_openjev.py).
os.environ.setdefault("HEATOS_JEV", "mock")
# Tests run on the synthetic building set (stable counts and scenarios); the app defaults to the team inventory.
os.environ.setdefault("HEATOS_BUILDINGS", "mock")
