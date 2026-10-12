"""Task 2: static checks on the Docker build context rules and Dockerfile.

The image build/run itself needs a Docker daemon and network, so it is validated
by the author in that environment. These tests lock the properties that keep
secrets and local state out of the image, which can be checked statically.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_dockerignore_excludes_secrets_and_db():
    ignore = _read(".dockerignore")
    lines = {line.strip() for line in ignore.splitlines()}
    for pattern in (".env", "*.db", "*.sqlite", ".venv/", ".git/"):
        assert pattern in lines, f"{pattern} should be in .dockerignore"


def test_dockerfile_runs_serve_and_does_not_copy_env():
    df = _read("Dockerfile")
    # Default command is the serve entry point.
    assert "stash" in df and "serve" in df
    # The model cache and data are volumes, not baked layers.
    assert "VOLUME" in df
    # No COPY of a secret or a database into the image.
    assert ".env" not in df.replace(".env.example", "")
