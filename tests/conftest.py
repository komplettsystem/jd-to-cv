import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The limiters keep state in memory for the life of the process; each test starts clean."""
    import web.auth as auth
    auth._hits.clear()
    auth._fails.clear()
    yield
