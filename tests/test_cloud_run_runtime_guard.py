from __future__ import annotations

import re

from scripts import cloud_run_runtime_guard as guard


def test_scheduler_job_pattern_includes_service_alias():
    pattern = guard._scheduler_job_pattern_for_services(["longbridge-quant-hk-service"])

    assert re.search(pattern, "longbridge-quant-hk-service-scheduler")
    assert re.search(pattern, "longbridge-quant-hk-scheduler")
    assert not re.search(pattern, "longbridge-quant-sg-scheduler")
