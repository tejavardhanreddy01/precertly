"""FastAPI entrypoint: health check, read-only policy endpoints, and (when enabled) live runs."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, FastAPI, HTTPException

from precertly import __version__
from precertly.live import mount_live
from precertly.live.routes import LiveMountOptions
from precertly.policies import Policy, load_policies
from precertly.settings import Settings, get_settings

router = APIRouter()


@lru_cache
def policies() -> dict[str, Policy]:
    return load_policies()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/policies")
def list_policies() -> list[dict[str, object]]:
    return [
        {
            "id": p.id,
            "title": p.title,
            "document": p.document,
            "variants": [{"id": v.id, "title": v.title, "codes": v.codes} for v in p.variants],
        }
        for p in policies().values()
    ]


@router.get("/policies/{policy_id}", response_model=Policy)
def get_policy(policy_id: str) -> Policy:
    policy = policies().get(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"unknown policy {policy_id}")
    return policy


@router.get("/requirements/{code}")
def requirements_for_code(code: str) -> list[dict[str, object]]:
    """Which policy variants and criteria apply to a CPT/HCPCS code (the future MCP tool)."""
    matches = [
        {
            "policy_id": p.id,
            "document": p.document,
            "variant_id": v.id,
            "variant": v.title,
            "criteria": [p.criterion(cid).text for cid in sorted(v.logic.criterion_ids())],
            "sources": [
                {"document": s.document, "version": s.version, "effective_date": s.effective_date}
                for s in p.sources
            ],
        }
        for p in policies().values()
        for v in p.variant_for_code(code)
    ]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no policy covers code {code}")
    return matches


def create_app(settings: Settings | None = None, live: LiveMountOptions | None = None) -> FastAPI:
    """The API. The live routes exist only when settings.live_enabled is true; with it off
    (the default) the app has no /live path at all and needs no database or AWS setting.
    `live` lets tests inject fakes."""
    settings = settings or get_settings()
    app = FastAPI(title="Precertly API", version=__version__)
    app.include_router(router)
    if settings.live_enabled:
        mount_live(app, settings, live)
    return app


app = create_app()
