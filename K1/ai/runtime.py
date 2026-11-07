from __future__ import annotations

from pathlib import Path


CPU_PROVIDER = "CPUExecutionProvider"
NPU_PROVIDER = "SpaceMITExecutionProvider"


def ordered_providers(provider: str | None, fallback: bool = True) -> list[str]:
    provider = (provider or CPU_PROVIDER).strip()
    providers: list[str] = []
    if provider == NPU_PROVIDER:
        try:
            __import__("spacemit_ort")  # registers SpaceMITExecutionProvider
        except Exception:
            if not fallback:
                raise
        else:
            providers.append(NPU_PROVIDER)
    elif provider:
        providers.append(provider)
    if fallback and CPU_PROVIDER not in providers:
        providers.append(CPU_PROVIDER)
    return providers


def create_session(
    model_path: str | Path,
    provider: str | None = None,
    fallback: bool = True,
    session_options=None,
    provider_options=None,
):
    import onnxruntime as ort

    providers = ordered_providers(provider, fallback=fallback)
    kwargs = {"providers": providers}
    if provider_options is not None:
        opts = [provider_options if p == NPU_PROVIDER else {} for p in providers]
        kwargs["provider_options"] = opts
    if session_options is not None:
        kwargs["sess_options"] = session_options
    return ort.InferenceSession(str(model_path), **kwargs)


def provider_label(provider: str | None) -> str:
    if provider == NPU_PROVIDER:
        return "spacemit"
    if provider == CPU_PROVIDER:
        return "cpu"
    if not provider:
        return "unknown"
    return provider.removesuffix("ExecutionProvider").lower()
