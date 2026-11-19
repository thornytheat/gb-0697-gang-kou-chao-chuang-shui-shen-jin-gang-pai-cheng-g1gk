from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.errors import object_payload
from app.modules.m8_booking import service

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent.parent / "templates")

@router.post("/api/8/plans")
def submit_plan(payload: object = Body(...)):
    return service.submit(object_payload(payload))

@router.post("/api/8/plans/cancel")
def cancel_plan(payload: object = Body(...)):
    return service.cancel(object_payload(payload))

@router.get("/api/8/plans")
def list_plans():
    return service.board()

@router.get("/api/8/plans/{plan_id}")
def plan_detail(plan_id: str):
    return service.get_plan(plan_id)

@router.get("/booking", response_class=HTMLResponse)
def booking_page(request: Request):
    return templates.TemplateResponse(request=request, name="booking.html",
                                      context={"title": "进港计划占窗工作台"})
