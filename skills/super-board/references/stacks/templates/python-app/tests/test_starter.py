"""Starter test from super-board's scaffold: runs the app's entry point."""
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def test_main_runs_and_prints():
    out = subprocess.run([sys.executable, str(MAIN)], capture_output=True, text=True, check=False)
    assert out.returncode == 0
    assert out.stdout.strip()
