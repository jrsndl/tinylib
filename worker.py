"""Deadline Python entry point; deploy alongside the tinylib package on a shared path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tinylib.log import configure
from tinylib.processing import process

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python worker.py manifest.json')
    configure(console=True)  # Deadline's task log shows the same lines as tinylib.log.
    process(sys.argv[1])
