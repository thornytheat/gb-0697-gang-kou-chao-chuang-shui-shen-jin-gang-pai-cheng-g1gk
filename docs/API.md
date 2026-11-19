# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV。`GET /health`：存活检查。

## M8 进港计划占窗（`/booking` 工作台）

`POST /api/8/plans`：提交/重新提交计划。字段 plan_id、vessel_id、segment、start、end（均须带时区）、request_key（幂等键，断网重试保持同键）。同一航段半开区间 `[start,end)` 同时刻仅一份计划生效；冲突返回 409 `PLAN_CONFLICT`，`details.conflicts` 列明冲突计划、船舶、航段与占用时段，本次提交仅留 CONFLICT 版本，任何已生效占位不变。调整受影响船舶后用**新幂等键**再次提交即生成新版本；旧版转 SUPERSEDED 但历史保留。

`POST /api/8/plans/cancel`：按 plan_id 取消生效占位并释放窗口，生成 CANCELLED 版本，支持 request_key 幂等。

`GET /api/8/plans`：看板，含 effective（当前生效占位）与 plans（各计划最新版本及状态 EFFECTIVE/CONFLICT/SUPERSEDED/CANCELLED，CONFLICT 行携带冲突回执）。`GET /api/8/plans/{plan_id}`：单计划全版本链。

写事务 `BEGIN IMMEDIATE` 串行化同库提交；request_key 唯一约束兜底并发，同键重复请求（含冲突结果）重放首次响应，不产生第二次占位。

错误：结构/数值422，版本冲突与占窗冲突409，不存在404，文件过大413。逐行异常进入quarantine。
