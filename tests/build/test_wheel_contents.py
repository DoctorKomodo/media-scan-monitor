"""Wheel-contents regression guard (contract §G).

Builds the project wheel and asserts that the non-.py runtime assets are
present in the zip namelist.  Hatchling's ``packages = ["mediascanmonitor"]``
already includes every file under the package — this guard asserts they STAY.

The test PASSES on a clean checkout (intentional: it is a regression guard,
not a TDD red-green exercise).  It would FAIL if, for example:
- ``mediascanmonitor/migrations/script.py.mako`` were deleted,
- the hatch build config were changed to exclude non-Python files, or
- the ``packages`` directive were removed or replaced with a narrower pattern.

Requirements
------------
``uv`` must be on PATH.  In this project that means: run ``uv sync`` first
(dev box) or let the CI ``setup-uv`` step install it.  The test is skipped
gracefully when uv is absent rather than erroring, so it never blocks a
``pytest`` run in environments that lack Docker-tooling but have Python.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

# Locate the repo root relative to this test file.
# tests/build/test_wheel_contents.py → parents[0]=tests/build/, [1]=tests/, [2]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Skip the entire module when uv is not available rather than producing an
# error that looks like a test failure.
pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv not on PATH — install via 'uv sync' or the CI setup-uv step",
)


def test_wheel_contains_runtime_assets(tmp_path: Path) -> None:
    """Build the wheel into tmp_path and assert every §G asset is present."""
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"uv build --wheel failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"Expected exactly one .whl in {tmp_path}, found: {wheels}"

    with zipfile.ZipFile(wheels[0]) as whl:
        names = whl.namelist()

    # -- Migration scripts -------------------------------------------------
    # env.py is the Alembic migration runner entry-point.
    assert "mediascanmonitor/migrations/env.py" in names, (
        "mediascanmonitor/migrations/env.py missing from wheel"
    )

    # script.py.mako is the highest-risk asset: it is the only non-.py file
    # in the migrations tree and the one most likely to be silently dropped
    # by a future build-config change that filters by extension.
    assert "mediascanmonitor/migrations/script.py.mako" in names, (
        "mediascanmonitor/migrations/script.py.mako missing from wheel — "
        "Alembic needs this template to generate new migration scripts"
    )

    # At least one versioned migration must ship so init_db can migrate to head.
    versions_present = [
        n
        for n in names
        if n.startswith("mediascanmonitor/migrations/versions/") and n.endswith(".py")
    ]
    assert versions_present, (
        "No mediascanmonitor/migrations/versions/*.py found in wheel — "
        "the app cannot migrate the DB on first boot"
    )

    # -- Jinja2 templates --------------------------------------------------
    # A missing template would pass the /health smoke test (the endpoint
    # does not render HTML) while silently serving a broken UI.
    templates_present = [
        n for n in names if n.startswith("mediascanmonitor/web/templates/") and n.endswith(".html")
    ]
    assert templates_present, (
        "No mediascanmonitor/web/templates/*.html found in wheel — the dashboard UI would be broken"
    )

    # -- Static assets -----------------------------------------------------
    assert "mediascanmonitor/web/static/app.css" in names, (
        "mediascanmonitor/web/static/app.css missing from wheel"
    )
    assert "mediascanmonitor/web/static/htmx.min.js" in names, (
        "mediascanmonitor/web/static/htmx.min.js missing from wheel"
    )

    # -- PEP 561 marker ----------------------------------------------------
    assert "mediascanmonitor/py.typed" in names, (
        "mediascanmonitor/py.typed missing from wheel — "
        "downstream type checkers would not find the inline types"
    )
