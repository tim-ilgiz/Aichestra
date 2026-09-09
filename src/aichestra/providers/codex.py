"""Codex preferred-lead provider — discovery only; never intercepts native CLI."""

from __future__ import annotations

from aichestra.providers.base import (
    FailureClass,
    ProviderAdapter,
    ProviderKind,
    ProviderRole,
    ProviderStatus,
    probe_version,
    which_binary,
)

_CODEX_BINARIES = ("codex",)


class CodexProvider(ProviderAdapter):
    kind = ProviderKind.CODEX

    def probe(self) -> ProviderStatus:
        binary = which_binary(_CODEX_BINARIES)
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="Codex binary not found on PATH",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.LEAD,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Codex available as preferred lead (native use unintercepted)",
            intercepts_native_cli=False,
        )
