"""Cloud identities are supplied by protected configuration, never source literals."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_sources_do_not_embed_cloud_project_identities():
    patterns = (
        re.compile(r'projects/[a-z0-9][a-z0-9-]{5,}/'),
        re.compile(r'@[a-z][a-z0-9-]{5,}\.iam\.gserviceaccount\.com'),
        re.compile(r'^\s*(?:GCP_PROJECT_ID|GOOGLE_CLOUD_PROJECT|project_id):\s*[a-z][a-z0-9-]{5,}\s*$', re.M),
    )
    locations = []
    for directory, suffix in (("scripts", ".py"), (".github/workflows", ".yml")):
        for path in (ROOT / directory).rglob("*" + suffix):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if any(pattern.search(line) for pattern in patterns):
                    locations.append(f"{path.relative_to(ROOT)}:{number}")
    assert not locations, "Cloud identity literals at " + ", ".join(locations)


@pytest.mark.parametrize("project,writer", [
    ("", ""), ("fake-project", ""), ("INVALID", "fake@fake-project.iam.gserviceaccount.com"),
    ("fake-project", "fake-runtime@other-project.iam.gserviceaccount.com"),
])
def test_metadata_config_rejects_unknown_or_mismatched_identity(project, writer):
    from scripts.inspect_hk_producer_metadata import MetadataResources
    with pytest.raises(ValueError, match="^metadata_config_invalid$"):
        MetadataResources(project, writer)


def test_missing_request_project_fails_before_transport(monkeypatch):
    from scripts import paper_request_readback as readback
    calls = []
    monkeypatch.setattr(readback, "PROJECT", "")
    with pytest.raises(ValueError, match="^config_invalid$"):
        readback.inspect("2026-11-02T01:00:00Z", "2026-11-02T01:01:00Z", run=lambda *a, **k: calls.append(True))
    assert calls == []
