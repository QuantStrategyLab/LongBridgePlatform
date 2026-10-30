"""Verify one immutable notification overlay without starting an image."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

BASE_COMMIT = "822037aae994c26a38586a46397f941de5b91a60"
OVERLAY_COMMIT = "0b59cf34b65ad66ad936828f0811a3bdbc5e9506"
PATCH_SHA256 = "95647d2cc91e7704fcf8c9c8dd42b31b4e49bb58601817ff2d9192e8d4e20429"
PATCH_PATH = "release-overlays/daily-notification/runtime.patch"
_PHASE = "input_validation"
FILES = (
    "application/account_new_risk_gate_support.py", "application/rebalance_service.py",
    "application/runtime_notification_adapters.py", "main.py",
    "notifications/digest_snapshot/__init__.py", "notifications/digest_snapshot/digest_archive.py",
    "notifications/digest_snapshot/digest_candidates.py", "notifications/digest_snapshot/digest_routing.py",
    "notifications/digest_snapshot/unified_contract.py", "notifications/display_names.json",
    "notifications/display_names.py", "notifications/unified.py", "notifications/unified_store.py",
    "scripts/emit_unified_digest_candidates.py", "scripts/execution_report_heartbeat.py",
)


class VerificationError(ValueError):
    def __init__(self, reason, path=None):
        super().__init__(reason)
        self.path = path if path in FILES else None


def _require(value):
    if not value:
        raise ValueError("notification_overlay_provenance_invalid")


def verify_build(build, files, dockerfile, digest, repo):
    """Check the exact builder instructions and embedded source, without rendering them."""
    steps = build.get("steps") or []
    tag = (build.get("results") or {}).get("images", [{}])[0].get("name", "")
    _require(tag.startswith(repo + ":") and len(tag) > len(repo) + 1)
    _require(build.get("status") == "SUCCESS" and not build.get("source") and len(steps) == 2)
    first, second = steps
    _require(not build.get("secrets") and not build.get("availableSecrets"))
    _require(not any((build.get("options") or {}).get(k) for k in ("env", "secretEnv", "volumes")))
    for step in steps:
        _require(not any(step.get(k) for k in ("env", "secretEnv", "volumes", "script", "dir")))
    args = first.get("args") or []
    _require(first.get("name") == "gcr.io/cloud-builders/git" and first.get("entrypoint") == "bash")
    _require(args[:3] == ["-c", 'printf %s "$@" | base64 -d | tar xz -C /workspace', "payload"] and len(args) > 3)
    _require(second.get("name") == "gcr.io/cloud-builders/docker" and not second.get("entrypoint"))
    _require(second.get("args") == ["build", "--network=none", "-t", tag, "/workspace"])
    images = (build.get("results") or {}).get("images") or []
    _require(len(images) == 1 and images[0].get("name") == tag and images[0].get("digest") == digest)
    try:
        raw = base64.b64decode("".join(args[3:]), validate=True)
        _require(len(raw) < 2 * 1024 * 1024)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            _require(len(members) == len(files) + 1 and all(m.isfile() for m in members))
            contents = {m.name: archive.extractfile(m).read() for m in members}
            _require(len(contents) == len(members) and contents == {**files, "Dockerfile": dockerfile})
    except (ValueError, OSError, tarfile.TarError, TypeError) as exc:
        raise ValueError("notification_overlay_provenance_invalid") from exc


def _run(args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, check=False)
    if result.returncode:
        if args[:3] == ["gcloud", "builds", "describe"]:
            reason = "build_metadata_permission_denied" if b"PERMISSION_DENIED" in result.stderr else "build_metadata_unavailable"
        else:
            reason = "verification_dependency_unavailable"
        raise RuntimeError(reason)
    return result.stdout


def _normalized_config(config):
    value = copy.deepcopy(config)
    value.pop("Image", None)
    defaults = {"AttachStderr": False, "AttachStdin": False, "AttachStdout": False,
                "OpenStdin": False, "StdinOnce": False, "Tty": False,
                "Domainname": "", "Hostname": ""}
    for key, expected in defaults.items():
        observed = value.pop(key, expected)
        _require(observed is None or (type(observed) is type(expected) and observed == expected))
    if value.get("User") in (None, ""):
        value.pop("User", None)
    return {k: v for k, v in value.items() if v is not None}



def verify_layer_list(baseline, candidate, expected_count):
    old_layers, new_layers = baseline["RootFS"]["Layers"], candidate["RootFS"]["Layers"]
    _require(new_layers[:len(old_layers)] == old_layers and len(new_layers) == len(old_layers) + expected_count)
    _require(_normalized_config(baseline["Config"]) == _normalized_config(candidate["Config"]))
    _require((baseline.get("Os"), baseline.get("Architecture")) ==
             (candidate.get("Os"), candidate.get("Architecture")))


def collect_layer(archive, files, found):
    parents = {"app"}
    for name in files:
        parent = Path("app") / name
        parents.update(str(p) for p in parent.parents if str(p) != ".")
    for member in archive:
        path = member.name.removeprefix("./").rstrip("/")
        if member.isdir():
            _require(path in parents and member.mode & 0o022 == 0)
            continue
        _require(member.isfile() and path.startswith("app/"))
        name = path.removeprefix("app/")
        if name == "notifications/digest_snapshot/.wh..wh..opq" and member.size == 0:
            continue
        _require(name in files and name not in found and member.uid == member.gid == 1000 and member.mode & 0o022 == 0)
        found[name] = archive.extractfile(member).read()

def verify(repo, project, base_digest, image_digest, build_id):
    """Verify Git, builder and OCI content; the containers are created but never started."""
    global _PHASE
    _PHASE = "input_validation"
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", base_digest) is not None)
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is not None)
    _require(re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", build_id) is not None)
    _PHASE = "patch_validation"
    patch = _run(["git", "show", f"{OVERLAY_COMMIT}:{PATCH_PATH}"])
    _require(hashlib.sha256(patch).hexdigest() == PATCH_SHA256)
    paths = [line.split(" b/", 1)[1] for line in patch.decode().splitlines() if line.startswith("diff --git a/")]
    _require(set(paths) == set(FILES) and len(paths) == len(FILES))
    base = repo + "@" + base_digest
    image = repo + "@" + image_digest
    dockerfile = ("FROM " + base + "\n" + "".join(
        f"COPY --chown=1000:1000 {name} /app/{name}\n" for name in FILES)).encode()
    containers = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source"
        source.mkdir()
        _PHASE = "baseline_source"
        archive = _run(["git", "archive", BASE_COMMIT])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # The Git archive is read from the fixed repository commit.
            tar.extractall(source, filter="data")
        patch_file = root / "runtime.patch"
        patch_file.write_bytes(patch)
        originals = {name: (source / name).read_bytes() for name in FILES if (source / name).is_file()}
        _run(["git", "apply", "--check", "--unidiff-zero", str(patch_file)], cwd=source)
        _run(["git", "apply", "--unidiff-zero", str(patch_file)], cwd=source)
        files = {name: (source / name).read_bytes() for name in FILES}
        _PHASE = "build_metadata"
        build = json.loads(_run(["gcloud", "builds", "describe", build_id, "--project", project, "--format=json"]))
        _require(build.get("id") == build_id and build.get("projectId") == project)
        _PHASE = "build_payload"
        verify_build(build, files, dockerfile, image_digest, repo)
        _PHASE = "image_read"
        for reference in (base, image):
            _run(["docker", "pull", reference])
        baseline, candidate = [json.loads(_run(["docker", "image", "inspect", ref]))[0] for ref in (base, image)]
        _PHASE = "image_config_and_layers"
        verify_layer_list(baseline, candidate, len(FILES))
        old_layers, new_layers = baseline["RootFS"]["Layers"], candidate["RootFS"]["Layers"]
        try:
            for ref in (base, image):
                containers.append(_run(["docker", "create", "--entrypoint", "/bin/true", ref]).decode().strip())
            _PHASE = "baseline_files"
            for name, content in originals.items():
                dest = root / "baseline-file"
                _run(["docker", "cp", containers[0] + ":/app/" + name, str(dest)])
                if dest.read_bytes() != content:
                    raise VerificationError("baseline_file_mismatch", name)
            # The only allowed opaque marker creates a directory absent in the base.
            absent = subprocess.run(["docker", "cp", containers[0] + ":/app/notifications/digest_snapshot", str(root / "absent")], capture_output=True)
            if absent.returncode == 0 or b"Could not find" not in absent.stderr:
                raise VerificationError("baseline_directory_absence_unconfirmed")
            _PHASE = "candidate_files"
            for name, content in files.items():
                dest = root / "candidate-file"
                _run(["docker", "cp", containers[1] + ":/app/" + name, str(dest)])
                _require(dest.read_bytes() == content)
            _PHASE = "candidate_layer_contents"
            saved = root / "candidate.tar"
            _run(["docker", "save", "-o", str(saved), image])
            found = {}
            with tarfile.open(saved) as outer:
                manifests = json.load(outer.extractfile("manifest.json"))
                _require(len(manifests) == 1)
                layers = manifests[0]["Layers"]
                _require(len(layers) == len(new_layers))
                for layer in layers[len(old_layers):]:
                    with tarfile.open(fileobj=outer.extractfile(layer)) as inner:
                        collect_layer(inner, files, found)
            _require(found == files)
        finally:
            for container in containers:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
    print(json.dumps({"status": "verified", "base_commit": BASE_COMMIT, "overlay_commit": OVERLAY_COMMIT,
                      "patch_sha256": PATCH_SHA256, "image_digest": image_digest, "file_count": len(FILES)}))


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 6:
        raise SystemExit("expected image repository, project, base digest, image digest and build id")
    try:
        verify(*sys.argv[1:])
    except Exception as exc:
        reasons = {"build_metadata_permission_denied", "build_metadata_unavailable", "verification_dependency_unavailable",
                   "baseline_file_mismatch", "baseline_directory_absence_unconfirmed"}
        reason = str(exc) if str(exc) in reasons else "overlay_verification_failed"
        result = {"status": "rejected", "phase": _PHASE, "reason": reason}
        if isinstance(exc, VerificationError) and exc.path:
            result["path"] = exc.path
        print(json.dumps(result), file=sys.stderr)
        raise SystemExit(1) from None
