"""Ensure the project root is importable as `core`, `pipeline`, `scraper` in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
