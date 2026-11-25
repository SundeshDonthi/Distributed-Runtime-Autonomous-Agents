'use strict';
const $ = (id) => document.getElementById(id);
let selectedId = null, currentTasks = [], generation = 0;
const placeholder = ['encode/httpx', 'psf/requests', 'aio-libs/aiohttp'].map((repo, i) => ({id:`preview_${i}`,description:`Collect ${repo}`,status:'PENDING',dependencies:[],input:{tool:'github.repository'}}));
placeholder.push({id:'preview_report',description:'Assemble evidence brief',status:'PENDING',dependencies:placeholder.map(t=>t.id),input:{tool:'github.compare'}});
function element(tag, text, cls) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
async function api(path, options={}) {
  const response=await fetch(path,options); let data;
  try{data=await response.json();}catch{throw new Error(`Server returned ${response.status}. Please retry.`);}
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status})`);
  return data;
}
function inspect(task) {
  selectedId=task.id;
  const root=$('inspector');root.replaceChildren(element('h4',task.description,'detail-title'));
  for(const [label,value] of [['Status',task.status],['Tool',task.input?.tool||'Unresolved'],['Retries',task.retry_count||0],['Replans',task.replan_count||0],['Dependencies',task.dependencies.length]]){
    const row=element('div',undefined,'detail-line');row.append(element('span',label),element('span',String(value)));root.append(row);
  }
  if(task.started_at){const duration=task.completed_at?((Date.parse(task.completed_at)-Date.parse(task.started_at))/1000).toFixed(3)+'s':'In progress';root.append(element('p',`Execution: ${duration}`,'muted'));}
  root.append(element('pre',JSON.stringify(task.output||{message:task.error||'Output will be checkpointed after execution.'},null,2)));
  document.querySelectorAll('.node').forEach(node=>node.classList.toggle('active',node.dataset.id===task.id));
}
function drawGraph(tasks) {
  currentTasks=tasks;
  const root=$('graph');root.replaceChildren();
  const byId=new Map(tasks.map(t=>[t.id,t])), depths=new Map();
  function depth(task,seen=new Set()){if(depths.has(task.id))return depths.get(task.id);if(seen.has(task.id))return 0;const next=new Set(seen).add(task.id);const d=task.dependencies.length?1+Math.max(...task.dependencies.map(id=>byId.has(id)?depth(byId.get(id),next):0)):0;depths.set(task.id,d);return d;}
  tasks.forEach(t=>depth(t));
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('connectors');root.append(svg);
  for(let level=0;level<=Math.max(0,...depths.values());level++){
    const col=element('div',undefined,'graph-column');
    for(const task of tasks.filter(t=>depths.get(t.id)===level)){
      const node=element('button',undefined,`node ${task.status}${task.id.includes('_readme_')?' added':''}`);node.type='button';node.dataset.id=task.id;
      node.append(element('small',task.id.includes('_readme_')?'NEW PREREQUISITE':`TASK / ${String(tasks.indexOf(task)+1).padStart(2,'0')}`),element('b',task.description),element('span',task.status.toLowerCase(),'status'));
      node.addEventListener('click',()=>inspect(task));col.append(node);
    }root.append(col);
  }
  requestAnimationFrame(connect);
  if(selectedId && byId.has(selectedId))inspect(byId.get(selectedId));
}
function connect(){
  const root=$('graph'),svg=root.querySelector('svg');if(!svg)return;svg.replaceChildren();const box=root.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${box.width} ${box.height}`);
  const nodes=new Map([...root.querySelectorAll('.node')].map(n=>[n.dataset.id,n]));
  for(const task of currentTasks)for(const dep of task.dependencies){const a=nodes.get(dep)?.getBoundingClientRect(),b=nodes.get(task.id)?.getBoundingClientRect();if(!a||!b)continue;
    const x=a.right-box.left,y=a.top+a.height/2-box.top,tx=b.left-box.left,ty=b.top+b.height/2-box.top;
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',`M${x},${y} C${(x+tx)/2},${y} ${(x+tx)/2},${ty} ${tx},${ty}`);path.setAttribute('fill','none');path.setAttribute('stroke','#bdc9bc');path.setAttribute('stroke-width','1.3');svg.append(path);
  }
}
function renderReport(tasks){
  const result=tasks.find(t=>t.output?.kind==='comparison_report')?.output;if(!result)return;
  $('report').hidden=false;$('report-source').textContent=result.source==='live'?'LIVE GITHUB EVIDENCE':'ILLUSTRATIVE OFFLINE EXAMPLE';$('report-cards').replaceChildren();
  for(const repo of result.repositories){const card=element('article',undefined,'report-card'),h=element('h3'),link=element('a',repo.repository+' ↗');link.href=repo.url;link.target='_blank';link.rel='noopener noreferrer';h.append(link);card.append(h,element('p',repo.description||'No description provided.'),element('div',`${repo.language||'Unknown'} · ${repo.license||'License unspecified'}`,'repo-meta'));
    if(repo.stars!==null)card.append(element('p',`${Number(repo.stars).toLocaleString()} stars · ${Number(repo.forks).toLocaleString()} forks`));$('report-cards').append(card);
  }$('methodology').textContent=result.methodology+(result.additional_evidence.length?' Recovery evidence: '+result.additional_evidence.map(r=>r.url).join(', '):'');
}
function showEvents(events){
  const root=$('events');root.replaceChildren();if(!events.length){root.append(element('li','Awaiting persisted execution events…','muted'));return;}
  for(const event of events){const li=element('li');li.append(element('time',new Date(event.timestamp).toLocaleTimeString([], {hour12:false})),element('span',event.type),element('span',currentTasks.find(t=>t.id===(event.detail.blocked_task_id||event.detail.aggregate_id||event.detail.payload?.task_id))?.description||''));root.append(li);}
}
async function follow(id, token){
  let failures=0;
  while(token===generation){
    try{
      const [workflow,events]=await Promise.all([api(`/workflows/${id}`),api(`/workflows/${id}/trace`)]);failures=0;
      $('run-state').textContent=workflow.status.toLowerCase();$('workflow-id').textContent=workflow.id.slice(0,19)+'…';$('workflow-id').title=workflow.id;
      $('version').textContent='v'+workflow.plan_version;$('completed').textContent=`${workflow.tasks.filter(t=>t.status==='COMPLETED').length} / ${workflow.tasks.length}`;
      const start=Date.parse(workflow.created_at),end=workflow.completed_at?Date.parse(workflow.completed_at):Date.now();$('elapsed').textContent=((end-start)/1000).toFixed(2)+'s';
      $('graph-caption').textContent=workflow.plan_version>1?'Graph extended · completed work preserved':'Independent collection → evidence assembly';
      drawGraph(workflow.tasks);showEvents(events);renderReport(workflow.tasks);
      if(['COMPLETED','FAILED','CANCELLED'].includes(workflow.status)){
        $('run').disabled=false;$('run').textContent='Run again ↗';if(workflow.status==='FAILED')throw new Error(workflow.tasks.find(t=>t.status==='FAILED')?.error||'Workflow failed. Inspect the task for details.');return;
      }
    }catch(error){failures++;if(failures>=3||!$('run').disabled){$('error').textContent=error.message;$('error').hidden=false;$('run').disabled=false;return;}}
    await new Promise(resolve=>setTimeout(resolve,650));
  }
}
$('run').addEventListener('click',async()=>{
  const token=++generation;$('run').disabled=true;$('run').textContent='Dispatching…';$('error').hidden=true;$('report').hidden=true;selectedId=null;
  $('run-state').textContent='dispatching';$('elapsed').textContent='—';$('completed').textContent='0 / 4';$('version').textContent='v1';$('workflow-id').textContent='Awaiting dispatch';$('events').replaceChildren(element('li','Submitting workflow…','muted'));$('inspector').replaceChildren(element('p','Select a task to inspect its persisted output.','muted'));drawGraph(placeholder);
  try{const workflow=await api('/demo/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:$('source').value,scenario:$('scenario').value})});history.replaceState(null,'',`/?run=${encodeURIComponent(workflow.id)}`);await follow(workflow.id,token);}
  catch(error){$('error').hidden=false;$('error').textContent=error.message;$('run').disabled=false;$('run').textContent='Try again ↗';}
});
$('source').addEventListener('change',()=>{$('source-note').textContent=$('source').value==='live'?'Fetches public metadata from api.github.com. GitHub availability and rate limits apply; failures are shown without substituting offline data.':'Offline examples use illustrative metadata. Changing statistics are omitted. Every task still executes through the runtime.';});
window.addEventListener('resize',connect);drawGraph(placeholder);
const saved=new URLSearchParams(location.search).get('run');if(saved && /^demo_[a-f0-9]{32}$/.test(saved)){ $('run').disabled=true;follow(saved,++generation); }

api('/demo/benchmark').then(result=>{
  if(result.scope!=='controlled_offline_fixture')return;
  $('benchmark').hidden=false;
  $('benchmark-latency').textContent=result.latency_reduction_percent.toFixed(1)+'%';
  $('benchmark-success').textContent='+'+result.success_improvement_percentage_points.toFixed(0)+' pp';
  $('benchmark-scope').textContent=`${result.workflow_pairs} paired workflows · seed ${result.seed} · ${result.injected_faults} injected missing prerequisites. ${result.success_counts.runtime}/${result.workflow_pairs} completed with recovery; ${result.success_counts.sequential}/${result.workflow_pairs} in the baseline.`;
}).catch(()=>{});
