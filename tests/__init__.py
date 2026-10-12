import os
import sys
from pathlib import Path

os.environ["EMF_DISABLED"] = "1"
os.environ["LOG_LEVEL"] = "CRITICAL"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opspilot.observability import get_logger  # noqa: E402

get_logger()
