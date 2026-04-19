"""
Unit tests for strategy.read_soc_cache() — the SOC file-cache helper
extracted from app.py._live_soc() (path 3 of 5).

These tests run without Flask, HA, or ESPHome dependencies.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from strategy import read_soc_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_soc_file(path: str, soc: float, age_s: float = 0.0):
    """Write a last_soc.json fixture with a timestamp `age_s` seconds in the past."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"soc": soc, "ts": time.time() - age_s}, f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadSocCache:
    def test_fresh_file_returns_soc(self, tmp_path):
        """Fresh cache (age < 300 s) returns the stored SOC value."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=72.5, age_s=10)
        assert read_soc_cache(p) == pytest.approx(72.5)

    def test_stale_file_returns_none(self, tmp_path):
        """Cache older than max_age_s → None."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=60.0, age_s=400)
        assert read_soc_cache(p, max_age_s=300) is None

    def test_custom_max_age(self, tmp_path):
        """max_age_s parameter is honoured — 60-s cache with 120-s window is valid."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=50.0, age_s=60)
        assert read_soc_cache(p, max_age_s=120) == pytest.approx(50.0)
        assert read_soc_cache(p, max_age_s=30) is None

    def test_missing_file_returns_none(self, tmp_path):
        """Non-existent file → None (no exception raised)."""
        p = str(tmp_path / "does_not_exist.json")
        assert read_soc_cache(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        """Corrupt JSON → None."""
        p = str(tmp_path / "last_soc.json")
        with open(p, "w") as f:
            f.write("not-json{{{")
        assert read_soc_cache(p) is None

    def test_missing_soc_key_returns_none(self, tmp_path):
        """JSON without 'soc' key → None."""
        p = str(tmp_path / "last_soc.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "value": 55.0}, f)
        assert read_soc_cache(p) is None

    def test_missing_ts_treated_as_epoch(self, tmp_path):
        """Missing 'ts' defaults to epoch 0 → always stale → None."""
        p = str(tmp_path / "last_soc.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"soc": 80.0}, f)
        assert read_soc_cache(p) is None

    def test_soc_above_100_returns_none(self, tmp_path):
        """SOC > 100.0 is invalid → None."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=101.0, age_s=5)
        assert read_soc_cache(p) is None

    def test_soc_below_0_returns_none(self, tmp_path):
        """SOC < 0.0 is invalid → None."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=-1.0, age_s=5)
        assert read_soc_cache(p) is None

    def test_soc_boundary_values_valid(self, tmp_path):
        """SOC exactly 0.0 and 100.0 are valid boundary values."""
        for soc in (0.0, 100.0):
            p = str(tmp_path / f"soc_{soc}.json")
            write_soc_file(p, soc=soc, age_s=1)
            assert read_soc_cache(p) == pytest.approx(soc)

    def test_returns_float(self, tmp_path):
        """Return type is always float (not int or str) when valid."""
        p = str(tmp_path / "last_soc.json")
        write_soc_file(p, soc=45, age_s=1)  # stored as int
        result = read_soc_cache(p)
        assert isinstance(result, float)
