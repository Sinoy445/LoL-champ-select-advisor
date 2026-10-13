import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from ui.overlay import run_overlay

if __name__ == "__main__":
    run_overlay()