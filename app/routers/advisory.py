"""Plain HTTP advisory endpoint — same logic path the WhatsApp handler uses,
exposed directly for testing/debugging without needing a WhatsApp round-trip."""
from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import AdvisoryRequest, AdvisoryResult
from app.services.advisory_service import get_advisory

router = APIRouter(prefix="/advisory", tags=["advisory"])


@router.post("", response_model=AdvisoryResult)
def post_advisory(req: AdvisoryRequest) -> AdvisoryResult:
    return get_advisory(req)
