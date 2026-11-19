const $=s=>document.querySelector(s), form=$('#plan-form'), notice=$('#notice');
const STATUS={EFFECTIVE:['生效','ok'],CONFLICT:['待调整','bad'],SUPERSEDED:['已替换','muted'],CANCELLED:['已取消','muted']};
function uuid(){return 'req-'+crypto.randomUUID()}
function isoLocal(s){if(!s)return '';return new Date(s*1000).toISOString().slice(0,19)}
function epoch(input){return Math.floor(new Date(input).getTime()/1000)}
function fmt(s){return new Date(s*1000).toISOString().replace('T',' ').slice(0,19)}
async function call(url,payload){
  const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)return {error:data,status:response.status};
  return {data};
}
function resetKey(){form.request_key.value=uuid()}
resetKey();
$('#new-key').addEventListener('click',resetKey);
function payloadFromForm(){
  return {plan_id:form.plan_id.value.trim(),vessel_id:form.vessel_id.value.trim(),
    segment:form.segment.value.trim(),start:new Date(form.start.value).toISOString(),
    end:new Date(form.end.value).toISOString(),request_key:form.request_key.value.trim()};
}
function showReceipt(details){
  const panel=$('#receipt-panel');panel.hidden=false;
  const plan=details.plan, rows=details.conflicts.map(c=>
    `<tr><td>${c.plan_id}</td><td>${c.vessel_id}</td><td>${c.segment}</td><td>${fmt(c.start_s)}</td><td>${fmt(c.end_s)}</td></tr>`).join('');
  $('#receipt-body').innerHTML=`<p>计划 <b>${plan.plan_id}</b>（${plan.vessel_id}）申请航段 <b>${plan.segment}</b> 的 ${fmt(plan.start_s)} ~ ${fmt(plan.end_s)} 未生效；原生效计划保持不变。冲突对象：</p>
    <table class="ledger"><thead><tr><th>冲突计划</th><th>占用船舶</th><th>航段</th><th>占用起 UTC</th><th>占用止 UTC</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">请只调整受影响船舶 ${plan.vessel_id} 的时段后用新幂等键重新提交。</p>`;
  panel.scrollIntoView({behavior:'smooth'});
}
async function submit(){
  if(!form.reportValidity())return;
  $('#submit-btn').disabled=true;notice.textContent='正在提交占窗…';
  const {data,error}=await call('/api/8/plans',payloadFromForm());
  $('#submit-btn').disabled=false;
  if(error){
    notice.textContent=error.message||'提交失败';
    if(error.error==='PLAN_CONFLICT'&&error.details)showReceipt(error.details);
    return;
  }
  $('#receipt-panel').hidden=true;
  notice.textContent=`计划 ${data.plan.plan_id} 已生效（版本 ${data.version}）`;
  await load();
}
form.addEventListener('submit',e=>{e.preventDefault();submit()});
$('#retry-btn').addEventListener('click',()=>{submit()});
function fillAdjust(row){
  form.plan_id.value=row.plan_id;form.vessel_id.value=row.vessel_id;
  form.segment.value=row.segment;form.start.value=isoLocal(row.start_s);
  form.end.value=isoLocal(row.end_s);resetKey();
  $('#form-title').textContent=`调整计划 ${row.plan_id}（只改受影响船舶的时段）`;
  form.scrollIntoView({behavior:'smooth'});form.start.focus();
}
async function cancel(planId){
  const key=uuid();
  const {data,error}=await call('/api/8/plans/cancel',{plan_id:planId,request_key:key});
  notice.textContent=error? (error.message||'取消失败'):`计划 ${planId} 已取消（版本 ${data.version}）`;
  await load();
}
async function load(){
  const board=await (await fetch('/api/8/plans')).json();
  const eb=$('#effective-body');
  eb.innerHTML=board.effective.length?board.effective.map(r=>
    `<tr><td>${r.plan_id}</td><td>${r.vessel_id}</td><td>${r.segment}</td><td>${fmt(r.start_s)}</td><td>${fmt(r.end_s)}</td><td class="num">v${r.version}</td></tr>`).join('')
    :'<tr><td>暂无生效占位</td></tr>';
  const pb=$('#plans-body');
  pb.innerHTML=board.plans.length?board.plans.map(r=>{
    const [label,cls]=STATUS[r.status]||[r.status,''];
    let cell='';
    if(r.status==='CONFLICT'){
      const c=(r.conflicts&&r.conflicts[0])||{};
      cell=`与 <b>${c.vessel_id||''}</b>（${c.plan_id||''}）冲突 ${c.start_s?fmt(c.start_s)+' ~ '+fmt(c.end_s):''} <button class="btn mini" data-act="adjust" data-id="${r.plan_id}">调整并重新提交</button>`;
    }else if(r.status==='EFFECTIVE'){
      cell=`<button class="btn mini" data-act="cancel" data-id="${r.plan_id}">取消占位</button>`;
    }else{
      cell='<span class="muted">—</span>';
    }
    return `<tr><td>${r.plan_id}</td><td>${r.vessel_id}</td><td>${r.segment}</td><td>${fmt(r.start_s)} ~ ${fmt(r.end_s)}</td><td class="num">v${r.version}</td><td class="${cls}">${label}</td><td>${cell}</td></tr>`;
  }).join(''):'<tr><td>暂无计划</td></tr>';
  pb.querySelectorAll('button[data-act]').forEach(btn=>btn.addEventListener('click',async()=>{
    const row=board.plans.find(p=>p.plan_id===btn.dataset.id);
    if(btn.dataset.act==='adjust')fillAdjust(row);
    if(btn.dataset.act==='cancel'&&confirm(`确认取消计划 ${row.plan_id} 的生效占位？`))await cancel(row.plan_id);
  }));
}
$('#refresh').addEventListener('click',load);
load();
