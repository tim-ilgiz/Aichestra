"""Cursor fallback-lead provider — discovery only; never intercepts native CLI."""

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

# Cursor agent / CLI names vary; probe common ones without wrapping them.
_CURSOR_BINARIES = ("cursor", "cursor-agent")


class CursorProvider(ProviderAdapter):
    kind = ProviderKind.CURSOR

    def probe(self) -> ProviderStatus:
        binary = which_binary(_CURSOR_BINARIES)
        if not binary:
            return ProviderStatus(
                kind=self.kind,
                available=False,
                role=ProviderRole.FALLBACK_LEAD,
                failure=FailureClass.UNAVAILABLE,
                detail="Cursor binary not found on PATH",
                intercepts_native_cli=False,
            )
        version = probe_version(binary)
        return ProviderStatus(
            kind=self.kind,
            available=True,
            role=ProviderRole.FALLBACK_LEAD,
            binary_path=binary,
            version=version,
            failure=FailureClass.NONE,
            detail="Cursor available as fallback lead (native use unintercepted)",
            intercepts_native_cli=False,
        )
