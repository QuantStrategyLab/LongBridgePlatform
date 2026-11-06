"""Guard credential and execution boundaries of the PAPER workflow routes."""

from pathlib import Path
import re
import subprocess

import pytest


WORKFLOWS = Path('.github/workflows')
READERS = ('inspect-paper-serving-metadata.yml', 'inspect-paper-dry-run-request.yml')


@pytest.mark.parametrize('name', READERS)
def test_cloud_routes_use_only_dedicated_configuration_and_no_environment(name):
    text = (WORKFLOWS / name).read_text()
    assert not re.search(r'^\s+environment:', text, re.M)
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "vars.PAPER_READBACK_ENABLED == 'true'" in text
    names = re.findall(r'\b(?:vars|secrets)\.([A-Z_]+)', text)
    assert names and all(name.startswith('PAPER_READBACK_') for name in names)
    assert 'PAPER readback-${{ github.ref_name }}' in text
    assert 'persist-credentials: false' in text
    assert 'ref: ${{ github.sha }}' in text
    assert text.index('GITHUB_WORKFLOW_SHA') < text.index('google-github-actions/auth@')
    assert not re.search(r'\b(curl|scheduler|deploy|update-traffic|add-iam-policy-binding)\b', text)


@pytest.mark.parametrize('name', READERS)
@pytest.mark.parametrize('matching', [True, False])
def test_source_mismatch_stops_before_authentication(tmp_path, name, matching):
    text = (WORKFLOWS / name).read_text()
    validation = text.split('      - name: Validate ', 1)[1].split('      - ', 1)[0]
    script = validation.split('        run: |\n', 1)[1]
    script = '\n'.join(line[10:] for line in script.splitlines())
    # Execute the real pre-auth script against a fake git executable. A mismatch
    # must stop before the simulated authentication marker, without a cloud call.
    git = tmp_path / 'git'
    git.write_text('#!/bin/sh\nprintf "%s\\n" "$GITHUB_SHA"\n')
    git.chmod(0o700)
    marker = tmp_path / 'authenticated'
    result = subprocess.run(
        ['/bin/bash', '-c', script + '\nprintf reached > "$AUTH_MARKER"'],
        env={'PATH': str(tmp_path), 'GITHUB_REF': 'refs/heads/main',
             'GITHUB_REPOSITORY': 'QuantStrategyLab/LongBridgePlatform',
             'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': ('a' if matching else 'b') * 40,
             'GCP_PROJECT_ID': 'synthetic-project', 'CLOUD_RUN_REGION': 'synthetic-region',
             'CLOUD_RUN_SERVICE': 'synthetic-paper-service', 'INSPECTION_MODE': 'serving-image',
             'GITHUB_ENV': str(tmp_path / 'env'), 'AUTH_MARKER': str(marker)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert (result.returncode == 0) is matching
    assert marker.exists() is matching


def test_offline_entry_has_no_cloud_configuration_or_write_permissions():
    text = (WORKFLOWS / 'validate-paper-offline.yml').read_text()
    assert not re.search(r'^\s+environment:', text, re.M)
    assert not re.search(r'\b(secrets|vars)\.', text)
    assert re.findall(r'^\s+([a-z-]+): (read|write|none)$', text, re.M) == [('contents', 'read')]
    assert 'google-github-actions/' not in text
    assert 'persist-credentials: false' in text
    assert 'uv sync --frozen --extra test' in text
    # Keep the blocked-submit behavior in the offline acceptance entry.
    assert 'tests/test_validation_cycle_observation.py' in text
    assert 'tests/test_plain_dca_paper_adapter.py' in text
    for test_path in re.findall(r'tests/test_[a-z_]+\.py', text):
        assert Path(test_path).is_file(), test_path
