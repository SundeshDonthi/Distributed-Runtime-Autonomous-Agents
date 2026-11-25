'use strict';
// The investigation view shares the runtime graph and inspector with the walkthrough.
const requirementLabels = {async_client:'Async client',http2:'HTTP/2',permissive_license:'Permissive license'};
const findingLabels = {met:'Supported',not_met:'Does not meet rule',not_established:'Not established'};
let investigationId = null, currentReport = null;
let candidates = ['encode/httpx','psf/requests','aio-libs/aiohttp'];
const terminal = status => ['COMPLETED','FAILED','CANCELLED'].includes(status);
function specification(){
  return {source:$('source').value,candidates:[...$('candidates').querySelectorAll('input:checked')].map(input=>input.value),requirements:[...$('requirements').querySelectorAll('input:checked')].map(input=>input.value)};
}
function sourceNote(){
  $('source-note').textContent=$('source').value==='live'?'Reads public GitHub metadata and one document per library. Missing statements remain unknown. GitHub rate limits apply.':'Example data uses illustrative metadata and excerpts. It does not describe current repository facts.';
}
function setBusy(busy){
  $('run').disabled=busy;$('revise').disabled=busy;
  for(const input of document.querySelectorAll('#requirements input, #candidates input, #source, #mode, #scenario'))input.disabled=busy;
}
function safeCitation(citation){
  try {const url=new URL(citation.url);return url.protocol==='https:' && ['github.com','api.github.com'].includes(url.hostname)?url.href:null;}catch{return null;}
}
function showFindings(report, previous){
  $('findings').hidden=!report;currentReport=report;if(!report)return;
  $('empty-state').hidden=true;
  $('finding-source').textContent=report.source==='live'?'Live GitHub':'Example data';
  $('conclusion').textContent=report.candidates_meeting_all_requirements.length?`Meets the evidence rules for all selected requirements: ${report.candidates_meeting_all_requirements.join(', ')}.`:report.conclusion;
  const table=element('table');table.append(element('caption','Results reflect only the sources collected for this run.'));
  const head=element('thead'),headRow=element('tr');
  for(const title of ['Library',...report.requirements.map(key=>requirementLabels[key])]){const cell=element('th',title);cell.scope='col';headRow.append(cell);}head.append(headRow);table.append(head);
  const body=element('tbody');
  for(const evaluation of report.evaluations){
    const row=element('tr'),name=element('th',evaluation.repository);name.scope='row';row.append(name);
    for(const requirement of report.requirements){const finding=evaluation.findings.find(item=>item.requirement===requirement),cell=element('td');cell.append(element('span',findingLabels[finding.status],`finding-status ${finding.status}`));row.append(cell);}body.append(row);
  }
  table.append(body);$('finding-table').replaceChildren(table);
  const root=$('finding-cards');
  // Preserve the reader's expanded evidence sections between polling updates.
  const expanded=new Set([...root.querySelectorAll('details[open]')].map(node=>node.dataset.repository));root.replaceChildren();
  for(const evaluation of report.evaluations){
    const card=element('details',undefined,'report-card');card.dataset.repository=evaluation.repository;card.open=expanded.has(evaluation.repository);card.append(element('summary',evaluation.repository));
    for(const finding of evaluation.findings){
      const section=element('section',undefined,'finding');section.append(element('h4',requirementLabels[finding.requirement]),element('span',findingLabels[finding.status],`finding-status ${finding.status}`),element('p',finding.explanation));
      for(const citation of finding.citations){
        section.append(element('blockquote',citation.excerpt));const href=safeCitation(citation);
        if(href){const link=element('a',citation.source==='snapshot'?'Repository reference':'Open source','citation');link.href=href;link.target='_blank';link.rel='noopener noreferrer';section.append(link);}
        if(citation.revision)section.append(element('small',`Document revision ${citation.revision.slice(0,12)}`,'revision-id'));
      }card.append(section);
    }root.append(card);
  }
  $('finding-limits').textContent=report.limitations.join(' ');
  const changes=$('changes');changes.replaceChildren();changes.hidden=!previous;if(!previous)return;
  changes.append(element('h3','Changes since the previous run'));
  if(previous.source!==report.source)changes.append(element('p','Evidence source changed; collection results were not reused.'));
  const removedLibraries=previous.evaluations.filter(old=>!report.evaluations.some(current=>current.repository===old.repository));
  if(removedLibraries.length)changes.append(element('p',`Removed libraries: ${removedLibraries.map(item=>item.repository).join(', ')}.`));
  const before=new Map(previous.evaluations.flatMap(e=>e.findings.map(f=>[`${e.repository}:${f.requirement}`,f.status])));
  let count=0;
  for(const evaluation of report.evaluations)for(const finding of evaluation.findings){
    const old=before.get(`${evaluation.repository}:${finding.requirement}`);
    if(old!==finding.status){count++;changes.append(element('p',`${evaluation.repository} / ${requirementLabels[finding.requirement]}: ${old?findingLabels[old]:'New requirement'} → ${findingLabels[finding.status]}`));}
  }
  const removed=previous.requirements.filter(key=>!report.requirements.includes(key));
  if(removed.length)changes.append(element('p',`Removed requirements: ${removed.map(key=>requirementLabels[key]).join(', ')}.`));
  if(!count)changes.append(element('p','Findings for retained requirements are unchanged. Evaluation and report assembly still executed again.'));
}
async function followInvestigation(id, token){
  let parentReport=null,parentLoaded=false,failures=0;
  while(token===generation){
    try{
      const [state,events]=await Promise.all([api(`/demo/investigations/${id}`),api(`/workflows/${id}/trace`)]);
      if(token!==generation)return;
      failures=0;investigationId=id;
      const workflow=state.workflow;
      if(state.parent_workflow_id&&!parentLoaded){
        // Parent history can expire on free hosting; the current report remains usable.
        parentLoaded=true;
        try{parentReport=(await api(`/demo/investigations/${state.parent_workflow_id}`)).report;}catch{parentReport=null;}
      }
      $('run-state').textContent=workflow.status.toLowerCase();$('workflow-id').textContent=id.slice(0,23)+'…';$('workflow-id').title=id;
      $('version').textContent='v'+workflow.plan_version;$('completed').textContent=`${workflow.tasks.filter(t=>t.status==='COMPLETED').length} / ${workflow.tasks.length}`;
      $('elapsed').textContent=((Date.parse(workflow.completed_at||new Date().toISOString())-Date.parse(workflow.created_at))/1000).toFixed(2)+'s';
      $('graph-caption').textContent=`Collect → evaluate → assemble · ${state.reused_checkpoints} checkpoints reused`;
      drawGraph(workflow.tasks);showEvents(events);showFindings(state.report,parentReport);
      if(terminal(workflow.status)){
        setBusy(false);$('run').textContent='New comparison';$('revision').hidden=false;
        $('revision-summary').textContent=state.reused_checkpoints?`${state.reused_checkpoints} collection tasks reused from the previous run. All findings were evaluated again.`:'Change the libraries or requirements, then update this comparison to reuse compatible evidence.';
        $('parent-link').hidden=!state.parent_workflow_id;if(state.parent_workflow_id)$('parent-link').href=`/?run=${state.parent_workflow_id}`;
        if(workflow.status==='FAILED'){$('empty-state').hidden=false;$('empty-state').replaceChildren(element('h3','Comparison could not finish'),element('p','See the error below or inspect the failed task.'));$('execution-details').open=true;}
        if(workflow.status==='FAILED')throw new Error(workflow.tasks.find(t=>t.status==='FAILED')?.error||'Investigation failed. Inspect its tasks before retrying.');
        return;
      }
    }catch(error){
      if(token!==generation)return;
      if(++failures>=3||!$('run').disabled){setBusy(false);$('error').hidden=false;$('error').textContent=error.message;return;}
    }
    await new Promise(resolve=>setTimeout(resolve,650));
  }
}
async function submitInvestigation(revision){
  const spec=specification();
  if(!spec.candidates.length){$('error').hidden=false;$('error').textContent='Select at least one library.';return;}
  if(!spec.requirements.length){$('error').hidden=false;$('error').textContent='Select at least one requirement.';return;}
  const parent=investigationId,token=++generation;
  candidates=spec.candidates;currentReport=null;$('action-status').textContent='';$('empty-state').hidden=false;$('empty-state').replaceChildren(element('h3','Collecting and checking evidence…'),element('p','The result will appear here. You can inspect task progress below.')); setBusy(true);$('error').hidden=true;$('report').hidden=true;$('findings').hidden=true;$('revision').hidden=true;selectedId=null;
  $('run-state').textContent='dispatching';$('elapsed').textContent='—';$('workflow-id').textContent='Awaiting dispatch';$('completed').textContent=`0 / ${spec.candidates.length*3+1}`;
  $('inspector').replaceChildren(element('p','Select a task to inspect evidence and checkpoint provenance.','muted'));
  $('events').replaceChildren(element('li','Submitting investigation…','muted'));$('graph').replaceChildren();
  try{
    const workflow=await api(revision?`/demo/investigations/${parent}/revisions`:'/demo/investigations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});
    history.replaceState(null,'',`/?run=${workflow.id}`);await followInvestigation(workflow.id,token);
  }catch(error){setBusy(false);$('run-state').textContent='Not submitted';$('empty-state').replaceChildren(element('h3','Comparison was not submitted'),element('p','Check the error above and try again.'));$('error').hidden=false;$('error').textContent=error.message;$('revision').hidden=!investigationId;}
}
$('run').addEventListener('click',()=>{if($('mode').value==='investigation')submitInvestigation(false);});
$('revise').addEventListener('click',()=>submitInvestigation(true));
$('mode').addEventListener('change',()=>{
  ++generation;selectedId=null;history.replaceState(null,'','/');$('error').hidden=true;const investigation=$('mode').value==='investigation';
  $('investigation-inputs').hidden=!investigation;$('empty-state').hidden=false;$('empty-state').replaceChildren(element('h3',investigation?'No comparison yet':'Runtime walkthrough'),element('p',investigation?'Choose libraries and requirements, then compare.':'Run the workflow to inspect collection, report assembly, and optional replanning.'));$('execution-details').open=!investigation;$('scenario-control').hidden=investigation;$('revision').hidden=true;$('findings').hidden=true;$('report').hidden=true;
  $('run').textContent=investigation?'Compare libraries':'Run workflow';$('run-state').textContent='Not run';drawGraph(investigation?investigationPreview():placeholder);$('completed').textContent=investigation?'0 / 10':'0 / 4';
});
function investigationPreview(){
  const tasks=[];
  for(const [index,repo] of candidates.entries()){
    const dependencies=['metadata','documentation'].map(kind=>{const id=`preview_${index}_${kind}`;tasks.push({id,description:`Collect ${kind}: ${repo}`,status:'PENDING',dependencies:[],input:{tool:'investigation.collect'}});return id;});
    tasks.push({id:`preview_${index}_evaluation`,description:`Evaluate requirements: ${repo}`,status:'PENDING',dependencies,input:{tool:'investigation.evaluate'}});
  }
  tasks.push({id:'preview_brief',description:'Assemble decision brief',status:'PENDING',dependencies:tasks.filter(task=>task.input.tool==='investigation.evaluate').map(task=>task.id),input:{tool:'investigation.assemble'}});
  return tasks;
}
$('source').addEventListener('change',sourceNote);sourceNote();
if(!saved){drawGraph(investigationPreview());$('completed').textContent='0 / 10';$('graph-caption').textContent='6 collection tasks → 3 evaluations → 1 decision brief';}

if(saved&&/^investigation_[a-f0-9]{32}$/.test(saved)){
  setBusy(true);
  api(`/demo/investigations/${saved}`).then(state=>{
    candidates=state.spec.candidates;for(const input of $('candidates').querySelectorAll('input'))input.checked=candidates.includes(input.value);$('source').value=state.spec.source;for(const input of $('requirements').querySelectorAll('input'))input.checked=state.spec.requirements.includes(input.value);
    sourceNote();return followInvestigation(saved,++generation);
  }).catch(error=>{setBusy(false);$('error').hidden=false;$('error').textContent=error.message+' Saved history may have expired after a host restart.';});
}else if(saved&&/^demo_[a-f0-9]{32}$/.test(saved)){
  $('mode').value='demo';$('investigation-inputs').hidden=true;$('empty-state').hidden=true;$('execution-details').open=true;$('scenario-control').hidden=false;
}

$('execution-details').addEventListener('toggle',()=>{if($('execution-details').open)requestAnimationFrame(connect);});
$('download-report').addEventListener('click',()=>{
  if(!currentReport)return;
  const blob=new Blob([investigationMarkdown(currentReport,location.href)],{type:'text/markdown;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download=`relay-${investigationId}.md`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  $('action-status').textContent='Report downloaded.';
});
$('copy-link').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText(location.href);$('action-status').textContent='Run link copied. Saved runs expire when the host restarts.';}
  catch{$('action-status').textContent='Copy the URL from your address bar. Clipboard access is unavailable.';}
});
