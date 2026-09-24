"""
Pytest configuration file for dm20-protocol tests.
"""

import sys
from pathlib import Path

# Add src directory to Python path for test imports
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))


import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Guarantee a current event loop for tests that call asyncio.get_event_loop().

    Earlier async tests (anyio / asyncio.run) leave the main thread with no
    current loop; on Python 3.12+ get_event_loop() then raises RuntimeError,
    which made ~140 unrelated tests fail only when the whole suite ran together.
    """
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
