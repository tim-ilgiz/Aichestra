"""Unit tests: machine profiler and local model selection / routing."""

from __future__ import annotations

from aichestra.local_runtime.base import LocalModel, ModelCapability
from aichestra.local_runtime.model_selector import select_model
from aichestra.machine_profiler import profile_machine
from aichestra.orchestration.media_routing import route_media


def test_machine_profiler_structured(repo_root) -> None:
    profile = profile_machine(root=repo_root)
    data = profile.to_dict()
    assert "os" in data
    assert "architecture" in data
    assert "cpu" in data
    assert "memory" in data
    assert "disks" in data
    assert "gpus" in data
    assert "local_ai" in data
    assert isinstance(profile.cpu.cores_logical, int)
    assert profile.memory.total_bytes >= 0
    assert profile.local_ai.local_enabled_default is False


def test_local_disabled_selection() -> None:
    models = [
        LocalModel(
            id="m",
            name="m",
            runtime="ollama",
            capabilities=frozenset({ModelCapability.TEXT}),
        )
    ]
    sel = select_model(models, local_enabled=False)
    assert sel.model is None
    assert "local.enabled=false" in sel.reason


def test_vision_capability_mismatch() -> None:
    text_only = LocalModel(
        id="llama",
        name="llama",
        runtime="ollama",
        capabilities=frozenset({ModelCapability.TEXT}),
    )
    sel = select_model(
        [text_only],
        local_enabled=True,
        required_capability=ModelCapability.VISION,
    )
    assert sel.model is None
    assert sel.axes["CAPABLE"] is False


def test_multi_model_prefers_preferred_id() -> None:
    models = [
        LocalModel(
            id="alpha",
            name="alpha",
            runtime="ollama",
            capabilities=frozenset({ModelCapability.TEXT}),
        ),
        LocalModel(
            id="beta",
            name="beta",
            runtime="ollama",
            capabilities=frozenset({ModelCapability.TEXT}),
        ),
    ]
    sel = select_model(
        models, local_enabled=True, preferred_ids=["beta"]
    )
    assert sel.model is not None
    assert sel.model.id == "beta"


def test_download_requires_explicit_approval() -> None:
    sel = select_model(
        [],
        local_enabled=True,
        download_candidate_id="big-model",
        allow_download=False,
    )
    assert sel.requires_download_approval is True
    assert sel.model is None

    approved = select_model(
        [],
        local_enabled=True,
        download_candidate_id="big-model",
        allow_download=True,
    )
    assert approved.requires_download_approval is False
    assert approved.model is not None
    assert approved.model.installed is False


def test_media_routing_refuses_text_only_local_for_vision() -> None:
    text_only = LocalModel(
        id="t",
        name="t",
        runtime="ollama",
        capabilities=frozenset({ModelCapability.TEXT}),
    )
    decision = route_media(
        ["shot.png"],
        local_model=text_only,
        local_enabled=True,
        prefer_orca_attachments=True,
    )
    assert decision.vision_required is True
    assert decision.provider_hint == "orca/cloud-vision"
    assert "text-only local refused" in decision.reason
