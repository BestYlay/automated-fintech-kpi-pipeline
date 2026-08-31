from datetime import date
from io import StringIO

import pytest

from fintech_pipeline.pipeline import _write_progress, bootstrap


def test_bootstrap_rejects_non_positive_workers():
    with pytest.raises(ValueError, match="workers must be at least 1"):
        bootstrap(date(2026, 1, 1), date(2026, 1, 1), workers=0)


def test_progress_log_contains_phase_eta_fields():
    output = StringIO()
    _write_progress(output, "mart", 2, 4, 0.0, 2)
    line = output.getvalue()
    assert "phase=mart" in line
    assert "workers=2" in line
    assert "completed=2/4" in line
    assert "estimated_remaining_seconds=" in line
    assert "ETA_UTC=" in line
