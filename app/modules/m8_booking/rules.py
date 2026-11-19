from app.errors import BusinessError
from app.timeops import instant
from decimal import Decimal

# 航段半开区间 [start, end)：结束同刻可开始下一航次，与 m4_channel 的 subtract 约定一致。
def parse_window(payload):
    if not isinstance(payload, dict):
        raise BusinessError("PAYLOAD", "计划须为JSON对象")
    plan_id = payload.get("plan_id")
    vessel = payload.get("vessel_id")
    segment = payload.get("segment")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise BusinessError("PLAN_ID", "计划编号须为非空文本")
    if not isinstance(vessel, str) or not vessel.strip():
        raise BusinessError("VESSEL", "船舶编号须为非空文本")
    if not isinstance(segment, str) or not segment.strip():
        raise BusinessError("SEGMENT", "航段编号须为非空文本")
    try:
        start = int(instant(payload.get("start")))
        end = int(instant(payload.get("end")))
    except BusinessError:
        raise
    if end <= start:
        raise BusinessError("WINDOW", "结束时刻须晚于开始时刻")
    request_key = payload.get("request_key")
    if request_key is not None and (not isinstance(request_key, str) or not request_key.strip()):
        raise BusinessError("REQUEST_KEY", "请求幂等键须为非空文本")
    return {
        "plan_id": plan_id.strip(), "vessel_id": vessel.strip(),
        "segment": segment.strip(), "start_s": start, "end_s": end,
        "request_key": request_key.strip() if request_key else None,
    }

def overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1

def find_conflicts(plan, active_rows):
    """active_rows: 同航段其它生效计划，返回全部重叠记录。"""
    hits = []
    for row in active_rows:
        if row["segment"] != plan["segment"]:
            continue
        if overlap(plan["start_s"], plan["end_s"], row["start_s"], row["end_s"]):
            hits.append({
                "plan_id": row["plan_id"], "vessel_id": row["vessel_id"],
                "segment": row["segment"], "start_s": row["start_s"], "end_s": row["end_s"],
            })
    return hits

def conflict_error(plan, hits):
    first = hits[0]
    return BusinessError(
        "PLAN_CONFLICT",
        f"航段 {plan['segment']} 在申请时段已被 {first['vessel_id']}（计划 {first['plan_id']}）占用",
        409,
        {"status": "CONFLICT", "plan": plan, "conflicts": hits},
    )
