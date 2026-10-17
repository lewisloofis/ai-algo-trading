"""Foreign-flow data tooling for plan 22 (Phase 1: universe, fetch, data checks). No returns here."""
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parents[1]
DAILY_DIR = RESEARCH_DIR / "data" / "daily"
DATA_DIR = RESEARCH_DIR / "data" / "foreign_flow"
UNIVERSE_CSV = DATA_DIR / "universe_monthly.csv"
MANIFEST_PATH = DATA_DIR / "_manifest.json"
