"""The project was renamed to Parity. `python -m ratchet ...` still works and forwards to `python -m parity ...`."""
import runpy
import sys

print("note: the project is now called Parity; use `python -m parity ...`", file=sys.stderr)
runpy.run_module("parity", run_name="__main__")
