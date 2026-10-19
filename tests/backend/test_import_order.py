"""Import-order independence.

`backend.core.__init__` used to eagerly re-export modules that import
`backend.services.*`, and `backend/services/__init__.py` imports
`strategy_service` -> `backend.core.risk_units`. That made the import graph
order-dependent:

    ImportError: cannot import name 'StrategyService' from partially
    initialized module 'backend.services.strategy_service' (most likely due to
    a circular import)

It stayed hidden because `backend.main` imports things in a working order, so
conftest-driven tests never triggered it. Any standalone import of a single
service — a maintenance script, a REPL, a one-off job on the VPS — could fail.

Each test runs in a FRESH interpreter; pytest's own import state would mask the
very thing being tested.
"""
import subprocess
import sys
import textwrap

import pytest

ENTRY_POINTS = [
    "backend.services.binance_service",
    "backend.services.strategy_service",
    "backend.services.auth_service",
    "backend.core.risk_units",
    "backend.core.daily_reset",
    "backend.core.position_manager",
    "backend.core.bot_engine",
    "backend.routers.performance",
    "backend.models.trade",
]


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_module_can_be_the_first_import(module):
    """Importing any single submodule first must succeed.

    Mutation: restore the eager re-exports in backend/core/__init__.py ->
    every service entry point raises ImportError.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"importing {module} first failed:\n{result.stderr[-1500:]}"
    )


def test_core_package_init_pulls_in_no_siblings():
    """The structural rule: `import backend.core` must not import services.

    If the package initialiser imports anything, it re-creates the cycle this
    whole file exists to prevent.
    """
    code = textwrap.dedent("""
        import sys
        import backend.core
        leaked = [m for m in sys.modules
                  if m.startswith("backend.services")]
        print(leaked)
    """)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-1500:]
    assert result.stdout.strip() == "[]", (
        "importing backend.core pulled in backend.services — reintroduces the "
        f"cycle. Got: {result.stdout.strip()}"
    )