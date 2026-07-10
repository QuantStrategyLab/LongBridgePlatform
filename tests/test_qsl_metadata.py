from __future__ import annotations

from pathlib import Path
import tomllib


def test_qsl_metadata_has_runtime_platform_fields() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "qsl.toml").open("rb") as f:
        qsl = tomllib.load(f)["qsl"]
    with (root / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)

    assert qsl["tier"] == "runtime"
    assert qsl["upgrade_ring"] == "ring_d"
    assert qsl.get("repo") == "LongBridgePlatform"
    assert qsl["compat"]["bundle"] == "2026.07.3"
    requires = qsl["requires"]
    assert "quant_platform_kit" in requires
    assert "us_equity_strategies" in requires
    assert "hk_equity_strategies" in requires

    dependency = next(
        value for value in project["project"]["dependencies"] if value.startswith("quant-platform-kit @ ")
    )
    qpk_pin = dependency.rsplit("@", maxsplit=1)[1]
    assert requires["quant_platform_kit"] == qpk_pin
    assert f"QuantPlatformKit.git?rev={qpk_pin}#{qpk_pin}" in (root / "uv.lock").read_text(encoding="utf-8")
