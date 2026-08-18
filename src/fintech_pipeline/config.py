from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_PATH = ROOT / "config" / "calibration.json"


@dataclass(frozen=True)
class Settings:
    pipeline_database_url: str
    timezone: str = "Asia/Hong_Kong"

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("PIPELINE_DATABASE_URL")
        if not url:
            raise RuntimeError("PIPELINE_DATABASE_URL is required")
        return cls(url, os.environ.get("PIPELINE_TIMEZONE", "Asia/Hong_Kong"))


def load_calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))

