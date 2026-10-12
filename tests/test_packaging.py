"""Task 0: the console-script entry point and packaged assets.

These do not build a wheel (that needs the toolchain); they assert the
declaration is correct and the data the installed app relies on is present.
"""

import os
import tomllib


def _pyproject():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def test_console_script_declared():
    data = _pyproject()
    scripts = data["project"]["scripts"]
    assert scripts.get("stash") == "stash.cli:main"


def test_entry_point_target_is_callable():
    from stash.cli import main
    assert callable(main)


def test_build_backend_is_hatchling():
    data = _pyproject()
    assert data["build-system"]["build-backend"] == "hatchling.build"
    assert "hatchling" in " ".join(data["build-system"]["requires"])


def test_web_assets_ship_with_the_package():
    import stash.web as web
    here = os.path.dirname(os.path.abspath(web.__file__))
    assert os.path.isdir(os.path.join(here, "templates"))
    assert os.path.isfile(os.path.join(here, "templates", "base.html"))
    assert os.path.isdir(os.path.join(here, "static"))
    assert os.path.isfile(os.path.join(here, "static", "app.css"))
