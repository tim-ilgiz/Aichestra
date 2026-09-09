"""Integration: local routing behaviors."""

from __future__ import annotations

from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.local_runtime.model_selector import select_model
from aichestra.orchestration.media_routing import route_media


def test_cloud_only_when_local_disabled() -> None:
    vision = LocalModel(
        id="llava",
        name="llava",
        runtime="ollama",
        capabilities=frozenset({ModelCapability.VISION, ModelCapability.TEXT}),
    )
    sel = select_model([vision], local_enabled=False, required_capability="vision")
    assert sel.model is None
    routed = route_media(["a.png"], local_model=vision, local_enabled=False)
    assert routed.provider_hint == "orca/cloud-vision"


def test_reuse_installed_over_download() -> None:
    installed = LocalModel(
        id="qwen2.5:14b",
        name="qwen2.5:14b",
        runtime="ollama",
        capabilities=frozenset({ModelCapability.TEXT}),
        parameter_size="14B",
    )
    sel = select_model(
        [installed],
        local_enabled=True,
        download_candidate_id="other-big",
        allow_download=False,
    )
    assert sel.ok
    assert sel.model is not None
    assert sel.model.id == "qwen2.5:14b"
    assert sel.reason.startswith("reusing")
