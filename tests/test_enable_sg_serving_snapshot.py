import copy
import json

import pytest

from scripts.enable_sg_serving_snapshot import FLAG, enable_snapshot

SOURCE = "2" * 40
SERVICE = "projects/example/locations/example/services/sg"
IMAGE = "example.invalid/source@sha256:" + "1" * 64


def fixture(*, enabled=False, drift=False, stale_template=False, already_staged=False, ready=True):
    old_env = [
        {"name": "RUNTIME_TARGET_ENABLED", "value": "true" if enabled else "false"},
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps({"platform_id": "longbridge", "account_scope": "SG"})},
    ]
    template = {"containers": [{"image": IMAGE, "env": old_env}], "serviceAccount": "example",
                "labels": {"commit-sha": SOURCE}}
    baseline = {"name": SERVICE, "ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
                "template": template, "etag": "before",
                "traffic": [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": "sg-old", "percent": 100}],
                "trafficStatuses": [{"revision": "sg-old", "percent": 100}]}
    old = copy.deepcopy(template)
    old["containers"][0]["name"] = "generated-container-name"
    if stale_template:
        template["containers"][0]["command"] = ["/different-command"]
    writes, live = [], copy.deepcopy(baseline)
    if already_staged:
        live["template"]["containers"][0]["env"].append({"name": FLAG, "value": "true"})
        live.update(etag="staged", latestReadyRevision="sg-old", latestCreatedRevision="sg-new")

    def request(path, method="GET", body=None):
        nonlocal live
        if method == "PATCH":
            writes.append((path, copy.deepcopy(body)))
            if "validateOnly" in path:
                return {"done": True}
            if path.endswith("updateMask=template"):
                assert body["etag"] == "before"
                live = {**live, "template": body["template"], "etag": "staged",
                        "latestReadyRevision": "sg-old", "latestCreatedRevision": "sg-new"}
            else:
                assert body["etag"] == "staged"
                live = {**live, "traffic": body["traffic"], "trafficStatuses": [{"revision": "sg-new", "percent": 100}]}
            return {"done": True}
        if path == SERVICE:
            return copy.deepcopy(live)
        if path.endswith("sg-old"):
            return copy.deepcopy(old)
        assert path.endswith("sg-new")
        data = {**old, "containers": copy.deepcopy(live["template"]["containers"])}
        data["containers"][0]["name"] = "generated-container-name"
        data["conditions"] = [{"type": "Ready", "state": "CONDITION_SUCCEEDED" if ready else "CONDITION_FAILED"}]
        if drift:
            data["containers"][0]["env"][0]["value"] = "true"
        return data

    return request, writes


def test_reuses_the_serving_image_and_preserves_other_environment_values():
    request, writes = fixture()
    enable_snapshot(request, SERVICE, SOURCE)
    assert len(writes) == 3
    staged = writes[1][1]["template"]["containers"][0]
    assert staged["image"] == IMAGE
    assert [entry for entry in staged["env"] if entry["name"] == FLAG] == [{"name": FLAG, "value": "true"}]
    assert staged["env"][0]["value"] == "false"
    assert writes[2][1]["traffic"][0]["revision"] == "sg-new"


@pytest.mark.parametrize(("options", "writes_before_stop"), [
    ({"enabled": True}, 0), ({"stale_template": True}, 0), ({"drift": True}, 2),
])
def test_rejects_enabled_trading_or_staged_configuration_drift(options, writes_before_stop):
    request, writes = fixture(**options)
    with pytest.raises(ValueError, match="readback_mismatch"):
        enable_snapshot(request, SERVICE, SOURCE)
    assert len(writes) == writes_before_stop


def test_rejects_a_serving_source_that_does_not_match_the_protected_pin():
    request, writes = fixture()
    with pytest.raises(ValueError, match="readback_mismatch"):
        enable_snapshot(request, SERVICE, "3" * 40)
    assert writes == []


def test_reuses_verified_zero_traffic_revision_after_an_interrupted_release():
    request, writes = fixture(already_staged=True)
    enable_snapshot(request, SERVICE, SOURCE)
    assert len(writes) == 1
    assert writes[0][0].endswith("updateMask=traffic")
    assert writes[0][1]["traffic"][0]["revision"] == "sg-new"


def test_never_routes_to_a_revision_that_is_not_ready():
    request, writes = fixture(already_staged=True, ready=False)
    with pytest.raises(ValueError, match="readback_mismatch"):
        enable_snapshot(request, SERVICE, SOURCE)
    assert writes == []
