"""Direct HTTP endpoint for ground-truth reports — lets you test/seed the
feedback loop without going through WhatsApp. The WhatsApp handler
(app/routers/whatsapp.py) hits app.services.ground_truth directly instead."""
from __future__ import annotations

from fastapi import APIRouter

from app.models.schemas import GroundTruthReportRequest
from app.services.ground_truth import record_ground_truth
from app.services.pastoralists import get_pastoralist, upsert_pastoralist

router = APIRouter(prefix="/ground-truth", tags=["ground-truth"])


@router.post("")
def post_ground_truth(req: GroundTruthReportRequest) -> dict:
    pastoralist = get_pastoralist(req.phone_number) or upsert_pastoralist(req.phone_number)
    record_ground_truth(pastoralist, req.report_type, req.report_text or req.report_type,
                         water_source_id=req.water_source_id)
    return {"status": "recorded"}
