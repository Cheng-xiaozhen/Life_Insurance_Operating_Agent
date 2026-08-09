"""Run the V3 template-analysis CLI from this Skill package."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from template_analysis_agent.cli import main


if __name__ == "__main__":
    main()
