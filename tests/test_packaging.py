"""Task 0: the console-script entry point and packaged assets.

These do not build a wheel (that needs the toolchain); they assert the
declaration is correct and the data the installed app relies on is present.
"""

import os

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore


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


def test_web_assets_present_on_disk():
    # The assets exist inside the package, where web.py resolves them at runtime
    # via os.path.dirname(__file__).
    import stash.web as web
    here = os.path.dirname(os.path.abspath(web.__file__))
    assert os.path.isfile(os.path.join(here, "templates", "board.html"))
    assert os.path.isfile(os.path.join(here, "templates", "login.html"))
    assert os.path.isfile(os.path.join(here, "static", "board.js"))
    assert os.path.isfile(os.path.join(here, "static", "board.css"))


def test_wheel_force_includes_web_assets():
    # Beyond hatchling's default inclusion, the assets are force-included into
    # the wheel so a later exclude rule cannot silently drop the runtime-critical
    # templates and static files. (force-include is additive; it does not
    # restrict the package's .py inclusion.)
    data = _pyproject()
    fi = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert fi.get("stash/templates") == "stash/templates"
    assert fi.get("stash/static") == "stash/static"


def test_wheel_has_no_exclude_dropping_assets():
    data = _pyproject()
    wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    for key in ("exclude", "only-include"):
        patterns = wheel.get(key, [])
        joined = " ".join(patterns)
        assert "templates" not in joined and "static" not in joined
