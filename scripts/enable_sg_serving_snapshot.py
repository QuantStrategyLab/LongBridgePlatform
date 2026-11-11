"""Enable the SG account snapshot on the verified serving image only."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FLAG = "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED"
API = "https://run.googleapis.com/v2/"
REVISION_METADATA = {
    "name", "uid", "generation", "createTime", "updateTime", "deleteTime", "expireTime",
    "launchStage", "service", "reconciling", "conditions", "observedGeneration", "logUri",
    "satisfiesPzs", "scalingStatus", "containerStatuses", "creator", "etag",
}
TEMPLATE_METADATA = {"revision", "client", "clientVersion"}


def _require(value):
    if not value:
        raise ValueError("sg_snapshot_release_readback_mismatch")


def _environment(container):
    return {item["name"]: item for item in container.get("env", [])}


def _revision_path(service, revision):
    if "/revisions/" in revision:
        _require(revision.startswith(service + "/revisions/"))
        return revision
    _require(re.fullmatch(r"[a-z0-9-]+", revision))
    return service + "/revisions/" + revision


def _require_matching_revision(template, revision):
    wanted = {k: v for k, v in template.items() if k not in TEMPLATE_METADATA}
    actual = {k: v for k, v in revision.items() if k not in REVISION_METADATA | TEMPLATE_METADATA}
    expected_container, = wanted["containers"]
    actual_container, = actual["containers"]
    if "name" not in expected_container:
        actual["containers"] = [{k: v for k, v in actual_container.items() if k != "name"}]
    _require(wanted == actual)


def enable_snapshot(request, service, approved_source):
    _require(re.fullmatch(r"[a-f0-9]{40}", approved_source))
    baseline = request(service)
    _require(baseline["name"] == service and baseline["ingress"] == "INGRESS_TRAFFIC_INTERNAL_ONLY")
    current_traffic, = baseline["traffic"]
    active, = [r for r in baseline["trafficStatuses"] if r.get("percent", 0) > 0]
    _require(current_traffic["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION")
    _require(current_traffic["percent"] == active["percent"] == 100)
    _require(not current_traffic.get("tag") and not active.get("tag"))
    old_path = _revision_path(service, active["revision"])
    _require(old_path == _revision_path(service, current_traffic["revision"]))
    old = request(old_path)
    _require(old.get("labels", {}).get("commit-sha") == approved_source)
    template = copy.deepcopy(baseline["template"])
    container, = template["containers"]
    _require_matching_revision(template, old)
    env = _environment(container)
    _require(re.search(r"@sha256:[a-f0-9]{64}$", container["image"]))
    _require(env["RUNTIME_TARGET_ENABLED"].get("value") == "false")
    target = json.loads(env["RUNTIME_TARGET_JSON"]["value"])
    _require(target["platform_id"] == "longbridge" and target["account_scope"].upper() == "SG")
    if FLAG in env:
        _require(env[FLAG].get("value") == "false" and "valueSource" not in env[FLAG])
        env[FLAG]["value"] = "true"
    else:
        container["env"].append({"name": FLAG, "value": "true"})
    template.pop("revision", None)
    body = {"name": service, "etag": baseline["etag"], "template": template}
    request(service + "?" + urlencode({"updateMask": "template", "validateOnly": "true"}), "PATCH", body)
    _require(request(service)["etag"] == baseline["etag"])
    staged = request(service + "?updateMask=template", "PATCH", body)
    _wait(request, staged)

    live = request(service)
    _require(live["traffic"] == baseline["traffic"] and live["trafficStatuses"] == baseline["trafficStatuses"])
    _require(live["ingress"] == baseline["ingress"] and live["template"]["serviceAccount"] == template["serviceAccount"])
    new_path = _revision_path(service, live["latestReadyRevision"])
    _require(new_path != old_path and new_path == _revision_path(service, live["latestCreatedRevision"]))
    ready = request(new_path)
    _require_matching_revision(template, ready)
    route = {**current_traffic, "revision": new_path.rsplit("/", 1)[-1], "percent": 100}
    moved = request(service + "?updateMask=traffic", "PATCH", {"name": service, "etag": live["etag"], "traffic": [route]})
    _wait(request, moved)

    final = request(service)
    current, = [r for r in final["trafficStatuses"] if r.get("percent", 0) > 0]
    _require(current["percent"] == 100 and _revision_path(service, current["revision"]) == new_path)
    _require(final["template"] == live["template"] and final["ingress"] == baseline["ingress"])


def _wait(request, operation):
    deadline = time.monotonic() + 240
    while not operation.get("done") and time.monotonic() < deadline:
        time.sleep(4)
        operation = request(operation["name"])
    _require(operation.get("done") and not operation.get("error"))


def main():
    project, region, service = (os.environ[k] for k in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    _require(all(re.fullmatch(r"[a-z0-9-]+", value) for value in (project, region, service)))
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True,
                                    stderr=subprocess.DEVNULL, timeout=30).strip()

    def request(path, method="GET", payload=None):
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(API + path, headers=headers, method=method, data=body), timeout=30) as response:
            result = json.load(response)
        _require(not result.get("error"))
        return result

    approved_source = os.environ["SG_SNAPSHOT_APPROVED_SERVING_SOURCE"]
    enable_snapshot(request, f"projects/{project}/locations/{region}/services/{service}", approved_source)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"sg_snapshot_release_verified": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print('{"sg_snapshot_release_verified":true,"serving_image_preserved":true,"trading_target_still_disabled":true}')
