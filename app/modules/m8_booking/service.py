import sqlite3, json, time
from datetime import datetime, timezone
from app.config import DATA_DIR
from app.errors import BusinessError
from app.modules.m8_booking.rules import parse_window, find_conflicts, conflict_error

EFFECTIVE, CONFLICT, SUPERSEDED, CANCELLED = "EFFECTIVE", "CONFLICT", "SUPERSEDED", "CANCELLED"

def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "bookings.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS plan_versions(
        plan_id TEXT NOT NULL, version INTEGER NOT NULL, vessel_id TEXT NOT NULL,
        segment TEXT NOT NULL, start_s INTEGER NOT NULL, end_s INTEGER NOT NULL,
        status TEXT NOT NULL, request_key TEXT, conflicts TEXT NOT NULL DEFAULT '',
        created TEXT NOT NULL, PRIMARY KEY(plan_id, version))""")
    # 生效占位：plan_id 唯一，保证一份计划至多一条生效版；区间重叠由事务内检查兜底
    db.execute("""CREATE TABLE IF NOT EXISTS bookings(
        plan_id TEXT PRIMARY KEY, version INTEGER NOT NULL, vessel_id TEXT NOT NULL,
        segment TEXT NOT NULL, start_s INTEGER NOT NULL, end_s INTEGER NOT NULL,
        created TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS requests(
        request_key TEXT PRIMARY KEY, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
        outcome TEXT NOT NULL, response TEXT NOT NULL, created TEXT NOT NULL)""")
    return db

def _now():
    return datetime.now(timezone.utc).isoformat()

def _raise_stored(outcome, response):
    if outcome == CONFLICT:
        raise BusinessError("PLAN_CONFLICT", response["message"], 409, response["details"])
    return response["body"]

def _replay(db, request_key):
    row = db.execute("SELECT outcome,response FROM requests WHERE request_key=?", (request_key,)).fetchone()
    if row is None:
        return None
    return _raise_stored(row["outcome"], json.loads(row["response"]))

def _wait_replay(key, attempts=20):
    # 并发同键：抢先事务提交前短暂读不到记录，退避等待其落库后重放
    for _ in range(attempts):
        with connect() as reread:
            replay = _replay(reread, key)
        if replay is not None:
            return replay
        time.sleep(0.02)
    return None

def _store_request(db, key, plan_id, version, outcome, response):
    if not key:
        return
    db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?,?)",
               (key, plan_id, version, outcome, json.dumps(response, ensure_ascii=False), _now()))

def _success_body(plan, version, superseded):
    body = {"status": EFFECTIVE, "version": version, "superseded_versions": superseded,
            "plan": {k: plan[k] for k in ("plan_id", "vessel_id", "segment", "start_s", "end_s")}}
    body["plan"]["version"] = version
    return body

def submit(raw):
    plan = parse_window(raw)
    key = plan["request_key"]
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        if key:
            replay = _replay(db, key)
            if replay is not None:
                db.commit()
                return replay
        row = db.execute("SELECT COALESCE(MAX(version),0) AS v FROM plan_versions WHERE plan_id=?",
                         (plan["plan_id"],)).fetchone()
        version = row["v"] + 1
        # 只与同航段其它计划的生效版比较；调整自身时排除自己的旧占位
        active = [dict(r) for r in db.execute(
            "SELECT plan_id,vessel_id,segment,start_s,end_s FROM bookings WHERE segment=? AND plan_id!=?",
            (plan["segment"], plan["plan_id"]))]
        hits = find_conflicts(plan, active)
        if hits:
            # 冲突留痕：仅新增一条 CONFLICT 版本，不动任何生效占位，旧版继续生效
            db.execute("INSERT INTO plan_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (plan["plan_id"], version, plan["vessel_id"], plan["segment"],
                        plan["start_s"], plan["end_s"], CONFLICT, key,
                        json.dumps(hits, ensure_ascii=False), _now()))
            err = conflict_error(plan, hits)
            _store_request(db, key, plan["plan_id"], version, CONFLICT,
                           {"message": str(err), "details": err.details})
            db.commit()
            raise err
        old = db.execute("SELECT version FROM bookings WHERE plan_id=?", (plan["plan_id"],)).fetchone()
        superseded = []
        if old:
            db.execute("UPDATE plan_versions SET status=? WHERE plan_id=? AND version=?",
                       (SUPERSEDED, plan["plan_id"], old["version"]))
            superseded.append(old["version"])
            db.execute("""UPDATE bookings SET version=?,vessel_id=?,segment=?,start_s=?,end_s=?,created=?
                          WHERE plan_id=?""",
                       (version, plan["vessel_id"], plan["segment"], plan["start_s"],
                        plan["end_s"], _now(), plan["plan_id"]))
        else:
            db.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,?)",
                       (plan["plan_id"], version, plan["vessel_id"], plan["segment"],
                        plan["start_s"], plan["end_s"], _now()))
        db.execute("INSERT INTO plan_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (plan["plan_id"], version, plan["vessel_id"], plan["segment"],
                    plan["start_s"], plan["end_s"], EFFECTIVE, key, "", _now()))
        body = _success_body(plan, version, superseded)
        _store_request(db, key, plan["plan_id"], version, EFFECTIVE, {"body": body})
        db.commit()
        return body
    except sqlite3.IntegrityError:
        # 并发下相同 request_key 抢先落库（BEGIN IMMEDIATE 的写锁可能在事务中才授予，
        # 事务内的早期重放检查未必看到同行）：回滚后重放既有结果，不产生第二次占位。
        db.rollback()
        if key:
            replay = _wait_replay(key)
            if replay is not None:
                return replay
        raise BusinessError("RETRY", "并发提交冲突，请凭 request_key 重试", 409)
    except sqlite3.OperationalError:
        # 写锁等待超时（并发量高时）：凭幂等键安全重试，无键则提示客户端带键重试
        db.rollback()
        if key:
            replay = _wait_replay(key)
            if replay is not None:
                return replay
        raise BusinessError("LOCKED", "系统繁忙，请凭 request_key 重试", 503)
    finally:
        db.close()

def cancel(raw):
    if not isinstance(raw, dict):
        raise BusinessError("PAYLOAD", "请求须为JSON对象")
    plan_id = raw.get("plan_id")
    key = raw.get("request_key")
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise BusinessError("PLAN_ID", "计划编号须为非空文本")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise BusinessError("REQUEST_KEY", "请求幂等键须为非空文本")
    plan_id, key = plan_id.strip(), key.strip() if key else None
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        if key:
            replay = _replay(db, key)
            if replay is not None:
                db.commit()
                return replay
        active = db.execute("SELECT * FROM bookings WHERE plan_id=?", (plan_id,)).fetchone()
        if active is None:
            raise BusinessError("NOT_ACTIVE", "该计划没有生效中的占位", 409)
        row = db.execute("SELECT COALESCE(MAX(version),0) AS v FROM plan_versions WHERE plan_id=?",
                         (plan_id,)).fetchone()
        version = row["v"] + 1
        db.execute("DELETE FROM bookings WHERE plan_id=?", (plan_id,))
        db.execute("INSERT INTO plan_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (plan_id, version, active["vessel_id"], active["segment"],
                    active["start_s"], active["end_s"], CANCELLED, key, "", _now()))
        body = {"status": CANCELLED, "version": version,
                "plan": {"plan_id": plan_id, "vessel_id": active["vessel_id"],
                         "segment": active["segment"], "start_s": active["start_s"],
                         "end_s": active["end_s"], "version": version}}
        _store_request(db, key, plan_id, version, CANCELLED, {"body": body})
        db.commit()
        return body
    except sqlite3.IntegrityError:
        db.rollback()
        if key:
            replay = _wait_replay(key)
            if replay is not None:
                return replay
        raise BusinessError("RETRY", "并发提交冲突，请凭 request_key 重试", 409)
    except sqlite3.OperationalError:
        db.rollback()
        if key:
            replay = _wait_replay(key)
            if replay is not None:
                return replay
        raise BusinessError("LOCKED", "系统繁忙，请凭 request_key 重试", 503)
    finally:
        db.close()

def _decode(row):
    item = {k: row[k] for k in row.keys()}
    item["conflicts"] = json.loads(item["conflicts"]) if item["conflicts"] else []
    return item

def board():
    with connect() as db:
        latest = [_decode(r) for r in db.execute("""
            SELECT v.* FROM plan_versions v
            JOIN (SELECT plan_id, MAX(version) AS mv FROM plan_versions GROUP BY plan_id) m
            ON v.plan_id=m.plan_id AND v.version=m.mv ORDER BY v.plan_id""")]
        effective = [dict(r) for r in db.execute(
            "SELECT plan_id,version,vessel_id,segment,start_s,end_s,created FROM bookings ORDER BY segment,start_s")]
    return {"effective": effective, "plans": latest}

def get_plan(plan_id):
    with connect() as db:
        rows = [_decode(r) for r in db.execute(
            "SELECT * FROM plan_versions WHERE plan_id=? ORDER BY version", (plan_id,))]
        active = db.execute("SELECT * FROM bookings WHERE plan_id=?", (plan_id,)).fetchone()
    if not rows:
        raise BusinessError("NOT_FOUND", "计划不存在", 404)
    return {"plan_id": plan_id, "active": dict(active) if active else None, "versions": rows}
