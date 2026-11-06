"""Build-time checks use installed VCS metadata, never a declared environment pin."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIN = "1d087fa424972d5d5ab02c4650ce9f039cb6711f"
OTHER = "a" * 40


@pytest.fixture
def check():
    return importlib.import_module("scripts.check_ues_revision")


@pytest.fixture
def manifests(tmp_path):
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    return tmp_path


def installed(check, monkeypatch, payload):
    monkeypatch.setattr(check.metadata, "distribution", lambda name: SimpleNamespace(
        read_text=lambda filename: payload,
    ))


def test_exact_installed_commit_passes(check, manifests, monkeypatch):
    installed(check, monkeypatch, json.dumps({"vcs_info": {"vcs": "git", "commit_id": PIN}}))
    assert check.verify(manifests) is None


@pytest.mark.parametrize("payload", [
    None, "", "{}", "null", "[]", "not json",
    '{"vcs_info": null}', '{"vcs_info": []}',
    json.dumps({"vcs_info": {"vcs": "git", "commit_id": OTHER}}),
    json.dumps({"vcs_info": {"vcs": "git", "commit_id": PIN[:12]}}),
    json.dumps({"vcs_info": {"vcs": "svn", "commit_id": PIN}}),
    json.dumps({"vcs_info": {"commit_id": PIN}}),
])
def test_missing_invalid_or_wrong_metadata_blocks(check, manifests, monkeypatch, payload):
    installed(check, monkeypatch, payload)
    monkeypatch.setenv("UES_REVISION", PIN)
    with pytest.raises(check.Incomplete):
        check.verify(manifests)


@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock"])
def test_manifest_disagreement_blocks(check, manifests, monkeypatch, filename):
    installed(check, monkeypatch, json.dumps({"vcs_info": {"vcs": "git", "commit_id": PIN}}))
    path = manifests / filename
    path.write_text(path.read_text().replace(PIN, OTHER))
    with pytest.raises(check.Incomplete):
        check.verify(manifests)


@pytest.mark.parametrize("filename", ["pyproject.toml", "uv.lock"])
def test_unreadable_manifest_blocks(check, manifests, filename):
    (manifests / filename).unlink()
    with pytest.raises(check.Incomplete):
        check.verify(manifests)


@pytest.mark.parametrize("error", [OSError("synthetic-private-detail"),
                                   importlib.metadata.PackageNotFoundError("synthetic-private-detail")])
def test_distribution_read_failure_blocks_without_diagnostics(check, manifests, monkeypatch, capsys, error):
    def fail(_name):
        raise error
    monkeypatch.setattr(check.metadata, "distribution", fail)
    monkeypatch.setattr(check, "ROOT", manifests)
    assert check.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "UES installed revision verification failed.\n"


def test_docker_build_checks_after_install():
    docker = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev \\\n    && uv run --no-sync python -m scripts.check_ues_revision" in docker
