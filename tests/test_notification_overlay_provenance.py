import base64
import io
import json
import tarfile

import pytest

from scripts.verify_notification_overlay import verify_build


def build_for(files, dockerfile, *, digest, repo):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in {**files, "Dockerfile": dockerfile}.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    encoded = base64.b64encode(stream.getvalue()).decode()
    tag = repo + ":notification634-closeout-20261030"
    return {"status": "SUCCESS", "source": None,
            "steps": [{"name": "gcr.io/cloud-builders/git", "entrypoint": "bash",
                       "args": ["-c", 'printf %s "$@" | base64 -d | tar xz -C /workspace', "payload", encoded]},
                      {"name": "gcr.io/cloud-builders/docker", "args": ["build", "--network=none", "-t", tag, "/workspace"]}],
            "results": {"images": [{"name": tag, "digest": digest}]}}


def test_build_provenance_accepts_exact_payload():
    files = {"notifications/unified.py": b"safe\n"}
    build = build_for(files, b"dockerfile", digest="sha256:abc", repo="example/repo")
    verify_build(build, files, b"dockerfile", "sha256:abc", "example/repo")


@pytest.mark.parametrize("mutation", ["extra_file", "changed_file", "wrong_digest", "changed_builder", "extra_step", "extra_env"])
def test_build_provenance_rejects_changed_inputs(mutation):
    files = {"notifications/unified.py": b"safe\n"}
    payload = dict(files)
    if mutation == "extra_file":
        payload["main.py"] = b"other"
    if mutation == "changed_file":
        payload["notifications/unified.py"] = b"other"
    build = build_for(payload, b"dockerfile", digest="sha256:abc", repo="example/repo")
    if mutation == "wrong_digest":
        build["results"]["images"][0]["digest"] = "sha256:wrong"
    if mutation == "changed_builder":
        build["steps"][1]["args"][1] = "--network=host"
    if mutation == "extra_step":
        build["steps"].append({"name": "unexpected"})
    if mutation == "extra_env":
        build["steps"][0]["env"] = ["EVIL=1"]
    with pytest.raises(ValueError):
        verify_build(build, files, b"dockerfile", "sha256:abc", "example/repo")


def test_overlay_source_requires_exact_paper_base_and_no_config_changes():
    from scripts.verify_deployed_runtime_target_admission import AdmissionError, _validate_image_only_source
    from scripts.verify_notification_overlay import BASE_COMMIT

    env = {"WORKFLOW_TARGET": "PAPER", "NOTIFICATION_OVERLAY_INPUT": "true",
           "OVERLAY_BASE_DIGEST": "sha256:" + "a" * 64, "OVERLAY_IMAGE_DIGEST": "sha256:" + "b" * 64,
           "OVERLAY_BUILD_ID": "12345678-1234-1234-1234-123456789abc"}
    assert _validate_image_only_source(image_commit=BASE_COMMIT, env=env, project="example") == (None, None)
    for changes, commit in [({"WORKFLOW_TARGET": "HK"}, BASE_COMMIT),
                            ({}, "a" * 40),
                            ({"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"}, BASE_COMMIT),
                            ({"NOTIFICATION_OVERLAY_INPUT": "invalid"}, BASE_COMMIT)]:
        with pytest.raises(AdmissionError):
            _validate_image_only_source(image_commit=commit, env={**env, **changes}, project="example")


def test_image_layers_and_config_are_bound_to_base():
    from scripts.verify_notification_overlay import verify_layer_list
    base = {"RootFS": {"Layers": ["base"]}, "Config": {"Env": ["x=y"]}, "Os": "linux", "Architecture": "amd64"}
    candidate = {"RootFS": {"Layers": ["base", "overlay"]}, "Config": {"Env": ["x=y"]}, "Os": "linux", "Architecture": "amd64"}
    verify_layer_list(base, candidate, 1)
    for change in ("extra", "base", "config", "architecture"):
        item = json.loads(json.dumps(candidate))
        if change == "extra": item["RootFS"]["Layers"].append("unexpected")
        if change == "base": item["RootFS"]["Layers"][0] = "different"
        if change == "config": item["Config"]["Env"] = ["x=z"]
        if change == "architecture": item["Architecture"] = "arm64"
        with pytest.raises(ValueError):
            verify_layer_list(base, item, 1)


@pytest.mark.parametrize("path", ["app/main.py", "etc/passwd", "app/notifications/.wh.unified.py"])
def test_layer_rejects_unapproved_files_and_whiteouts(path):
    from scripts.verify_notification_overlay import collect_layer
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        entry = tarfile.TarInfo(path)
        entry.uid = entry.gid = 1000
        entry.mode = 0o644
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    with tarfile.open(fileobj=io.BytesIO(stream.getvalue())) as archive:
        with pytest.raises(ValueError):
            collect_layer(archive, {"notifications/unified.py": b"x"}, {})
