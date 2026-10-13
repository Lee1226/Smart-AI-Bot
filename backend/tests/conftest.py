"""Reset process-local auth throttle between independent integration cases."""
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    from routers.auth import _attempts
    _attempts.clear()
