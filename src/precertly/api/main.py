"""FastAPI entrypoint. Day 1: health check and read-only policy endpoints."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException

from precertly import __version__
from precertly.policies import Policy, load_policies

app = FastAPI(title="Precertly API", version=__version__)


@lru_cache
def policies() -> dict[str, Policy]:
    return load_policies()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/policies")
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


@app.get("/policies/{policy_id}", response_model=Policy)
def get_policy(policy_id: str) -> Policy:
    policy = policies().get(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"unknown policy {policy_id}")
    return policy


@app.get("/requirements/{code}")
def requirements_for_code(code: str) -> list[dict[str, object]]:
    """Which policy variants and criteria apply to a CPT/HCPCS code (the future MCP tool)."""
    matches = [
        {
            "policy_id": p.id,
            "document": p.document,
            "variant_id": v.id,
            "variant": v.title,
            "criteria": [p.criterion(cid).text for cid in sorted(v.logic.criterion_ids())],
        }
        for p in policies().values()
        for v in p.variant_for_code(code)
    ]
    if not matches:
        raise HTTPException(status_code=404, detail=f"no policy covers code {code}")
    return matches
