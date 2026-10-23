"""Test configuration shared by the whole suite.

The suite runs against a local Postgres (see tests/localdb.py), never the hosted database. Tests that call the real Gemini API are
marked `live` and only run with `--live`.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))




def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="also run tests that call the real Gemini API")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the real Gemini API (needs a key and daily quota); run with --live")
    from tests.localdb import local_database_url

    # Must happen before anything reads settings: environment variables win over backend/.env.
    os.environ["DATABASE_URL"] = local_database_url()
    os.environ["GEMINI_MODE"] = "live"  # tests inject their own fake gateways; this only keeps a stray settings read from replaying


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="calls the real Gemini API; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
