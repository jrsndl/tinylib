"""Deadline Python entry point; deploy alongside the tinylib package on a shared path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tinylib.processing import process

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python worker.py manifest.json')
    process(sys.argv[1])
