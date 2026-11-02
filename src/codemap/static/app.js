'use strict';
const $ = id => document.getElementById(id);
const COLORS = {module:'#68aeff', class:'#ebc77b', function:'#71d7bf', method:'#c7a2fa'};
const RANK = {module:0, class:1, function:2, method:2};
let data = null, cy = null, selected = null, history = [], asking = false, analyzing = false;
let generation = 0, sourceRequest = 0, sourcePage = null;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function button(text, callback, className) {
  const b = element('button', text, className); b.type = 'button'; b.onclick = callback; return b;
}
async function request(url, body) {
  const controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),65000);
  try {
    const response = await fetch(url, {signal:controller.signal, ...(body === undefined ? {} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    })});
    let payload;
    try { payload = await response.json(); } catch { throw new Error('Server returned an unexpected response. Retry or restart the app.'); }
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `Request failed (${response.status})`);
    return payload;
  } catch (error) {
    if (error.name==='AbortError') throw new Error('Request timed out. Try a smaller repository or a more focused question.');
    if (error instanceof TypeError) throw new Error('Backend unavailable. Run start.ps1 in the project folder, then click Reconnect.');
    throw error;
  } finally { clearTimeout(timer); }
}
function updateControls() {
  $('sendBtn').disabled = !data || asking || analyzing;
  $('analyzeBtn').disabled = analyzing;
  for (const id of ['refreshBtn','exportGraph','exportReport','resetMap']) $(id).disabled = !data || analyzing;
}
function clearSelection() {
  selected = null; sourcePage = null; sourceRequest++;
  $('nodeActions').hidden=true; $('relations').replaceChildren(); $('exploration').hidden=true; $('pathDetails').hidden=true;
  $('clearSelection').hidden = true;
  $('nodeDetails').textContent = 'Select a node or an answer citation to inspect its source.';
  $('sourceCode').hidden = true; $('sourceNavigation').hidden = true;
  $('sourceHeading').textContent = ''; $('chatContext').textContent = 'Context: entire repository';
  if (cy) { cy.elements().unselect(); cy.elements().removeClass('evidence'); }
}
async function analyze(event) {
  event.preventDefault();
  const path = $('pathInput').value.trim(); if (!path || analyzing) return;
  analyzing = true; updateControls(); $('status').textContent = 'Analyzing repository…';
  try {
    const next = await request('/api/analyze', {path});
    generation++; data = next; history = []; transcript=[]; asking = false;
    showWelcome(); $('symbolSearch').value = ''; $('searchResults').replaceChildren();
    focusSet=null; clearSelection(); $('lod').value = next.nodes.length>60?'module':'all'; renderGraph(); populateExplorer(); loadBaselines();
    rememberPath(path); $('mapTitle').textContent=next.repository_name || 'Dependency map'; showPanel('map');
    $('status').textContent = `${next.stats.files_parsed} files analyzed · ${next.stats.files_failed} failed`;
  } catch (error) { $('status').textContent = error.message; }
  finally { analyzing = false; updateControls(); }
}
function renderGraph() {
  $('mapEmpty').hidden = true;
  if (typeof cytoscape === 'undefined') {
    $('mapEmpty').hidden = false;
    $('mapEmpty').replaceChildren(element('p', 'Map library unavailable. Chat and source inspection still work.'));
    updateStats(); return;
  }
  if (cy) cy.destroy();
  const scale = Math.max(250, Math.sqrt(data.nodes.length) * 60);
  const elements = data.nodes.map(n => ({data:{id:n.id,label:n.name,type:n.type,node:n},position:{x:n.x*scale,y:n.y*scale}}));
  data.edges.forEach((e,i) => elements.push({data:{id:`edge:${i}`,source:e.source,target:e.target,etype:e.type,edge:e}}));
  cy = cytoscape({container:$('cy'),elements,layout:{name:'preset',padding:40},minZoom:.05,maxZoom:4,style:[
    {selector:'node',style:{'background-color':el=>COLORS[el.data('type')],label:'data(label)',color:'#dce8f6','font-size':11,'text-wrap':'wrap','text-max-width':110,'min-zoomed-font-size':8,'text-opacity':0,'text-valign':'bottom','text-margin-y':6,'text-outline-color':'#0b1018','text-outline-width':2,width:el=>el.data('type')==='module'?24:16,height:el=>el.data('type')==='module'?24:16,'border-width':el=>el.data('node').is_entrypoint?3:0,'border-color':'#ebc77b'}},
    {selector:'edge',style:{width:1,'line-color':'#334356','target-arrow-color':'#334356','target-arrow-shape':'triangle','curve-style':'bezier',opacity:.65}},
    {selector:'edge[etype="calls"]',style:{'line-color':'#477669','target-arrow-color':'#71d7bf'}},
    {selector:'node[type="module"], node[type="class"], node.hover, node:selected, node.evidence',style:{'text-opacity':1}},
    {selector:'edge.evidence',style:{'line-color':'#74dbc1','target-arrow-color':'#74dbc1',width:2}},
    {selector:'.evidence',style:{'border-color':'#71d7bf','border-width':4}},
    {selector:'node:selected',style:{'border-color':'#fff','border-width':3}},
    {selector:'.filtered',style:{display:'none'}}
  ]});
  cy.on('tap','node',event => selectNode(event.target.id()));
  cy.on('tap','edge',event=>showRelationship(event.target.data('edge')));
  cy.on('mouseover','node',event=>event.target.addClass('hover'));
  cy.on('mouseout','node',event=>event.target.removeClass('hover'));
  applyDetail(); arrangeVisible();
}
function updateStats() {
  if (!data) return;
  const rank = {module:0,class:1,all:2}[$('lod').value];
  const visible = data.nodes.filter(n=>RANK[n.type]<=rank && (!focusSet || focusSet.has(n.id))).length;
  $('stats').textContent = `${visible}/${data.stats.node_count} nodes · ${data.edges.length} edges · ${data.stats.unresolved_calls} unresolved calls`;
}
function applyDetail() {
  if (cy) {
    const rank = {module:0,class:1,all:2}[$('lod').value];
    cy.batch(()=>{
      cy.nodes().forEach(n=>n.toggleClass('filtered',RANK[n.data('type')]>rank || (focusSet && !focusSet.has(n.id()))));
      cy.edges().forEach(e=>e.toggleClass('filtered',e.source().hasClass('filtered')||e.target().hasClass('filtered')||($('edgeFilter').value!=='all' && e.data('etype')!==$('edgeFilter').value)));
    });
  }
  updateStats();
}
function highlight(ids) {
  if (!cy) return;
  focusSet=null; $('focusBar').hidden=true; $('lod').value = 'all'; applyDetail();
  cy.elements().removeClass('evidence');
  const nodes = cy.nodes().filter(n=>ids.includes(n.id()));
  nodes.addClass('evidence');
  if (nodes.length) { cy.fit(nodes,90); if (cy.zoom()>1.6) cy.zoom(1.6); cy.center(nodes); }
}
function showSource(citation) {
  $('sourceHeading').textContent = `${citation.file}:${citation.start_line}–${citation.end_line}${citation.truncated?' · excerpt':''}`;
  $('sourceCode').textContent = citation.snippet.split('\n').map((line,i)=>`${String(citation.start_line+i).padStart(4)}  ${line}`).join('\n');
  $('sourceCode').hidden = false;
}
async function loadSource(start) {
  if (!data || !selected) return;
  const ticket = ++sourceRequest, current = generation, id = selected;
  const params = new URLSearchParams({analysis_id:data.analysis_id,node_id:id});
  if (start !== undefined) params.set('start_line',start);
  try {
    const result = await request('/api/source?'+params);
    if (ticket!==sourceRequest || current!==generation || selected!==id) return;
    sourcePage = result; showSource(result);
    const node = data.nodes.find(n=>n.id===id);
    $('sourceNavigation').hidden = false;
    $('prevSource').disabled = result.start_line <= node.lineno;
    $('nextSource').disabled = result.end_line >= node.end_lineno;
  } catch (error) { if (ticket===sourceRequest) $('sourceHeading').textContent = error.message; }
}
function selectNode(id, citation) {
  const node = data?.nodes.find(n=>n.id===id); if (!node) return;
  selected = id; sourceRequest++; sourcePage = null;
  explorationTicket++; showPanel('inspect'); $('nodeActions').hidden=false; $('pathDetails').hidden=false; $('exploration').hidden=true; renderRelations(node);
  $('clearSelection').hidden = false;
  $('chatContext').textContent = 'Context: '+node.qualified_name;
  $('nodeDetails').replaceChildren(element('strong',node.qualified_name),element('p',`${node.type}${node.is_entrypoint?' · entry point':''}`,'muted'));
  $('nodeDetails').append(button('Explain in chat',()=>submitQuestion('Explain this selected symbol and its relationships, citing the source.')));
  if (citation) $('nodeDetails').append(button('Browse full symbol',()=>loadSource()));
  $('sourceCode').hidden = true; $('sourceNavigation').hidden = true; $('sourceHeading').textContent = 'Loading source…';
  if (cy) { cy.elements().unselect(); cy.getElementById(id).select(); }
  if (citation) showSource(citation); else loadSource();
}
function openCitation(citation, analysisId) {
  if (data?.analysis_id!==analysisId) return;
  if (citation.node_id) { highlight([citation.node_id]); selectNode(citation.node_id,citation); }
  else { sourceRequest++; showSource(citation); $('sourceNavigation').hidden=true; }
}
function addMessage(role,text) {
  $('chatMessages').querySelector('.welcome')?.remove();
  const box = element('div',undefined,'message '+role);
  box.append(element('div',role==='user'?'You':'Cartographer','message-label'));
  box.append(element('div',text,'answer')); $('chatMessages').append(box);
  $('chatMessages').scrollTop = $('chatMessages').scrollHeight; return box;
}
function displayAnswer(box,result,analysisId) {
  const answer = box.querySelector('.answer'); answer.replaceChildren();
  const citations = new Map(result.citations.map(c=>[c.id,c]));
  result.answer.split(/(\[S\d+\])/g).forEach(part=>{
    const citation = citations.get(part.slice(1,-1));
    if (citation) answer.append(button(part,()=>openCitation(citation,analysisId),'citation-inline'));
    else answer.append(document.createTextNode(part));
  });
  const links = element('div',undefined,'citations');
  result.citations.forEach(c=>links.append(button(`${c.id} · ${c.file}:${c.start_line}`,()=>openCitation(c,analysisId))));
  if (result.highlight_node_ids.length) links.append(button('Highlight sources',()=>highlight(result.highlight_node_ids)));
  box.append(links);
  const reasons = {not_configured:'Local retrieval · Configure a model and API key for AI answers.',provider_error:'Model request failed · Showing local evidence.',invalid_response:'Model returned an unverified answer · Showing local evidence.',budget_exhausted:'Model reached its retrieval limit · Showing local evidence.'};
  box.append(element('div',result.source==='llm'?'AI answer · Source references checked; verify the interpretation.':reasons[result.fallback_reason]||'Local retrieval','answer-note'));
  highlight(result.highlight_node_ids);
}
async function submitQuestion(question) {
  question = question.trim(); if (!question || !data || asking || analyzing) return;
  showPanel('chat'); asking = true; updateControls(); $('questionInput').value = '';
  const current = generation, analysisId = data.analysis_id;
  const previous = history.slice(-8);
  addMessage('user',question); const box = addMessage('assistant','Finding source evidence…');
  try {
    const result = await request('/api/chat',{analysis_id:analysisId,question,selected_node_id:selected,history:previous});
    if (current!==generation) return;
    displayAnswer(box,result,analysisId);
    transcript.push({question,...result}); transcript=transcript.slice(-50);
    history.push({role:'user',content:question},{role:'assistant',content:result.answer});
    history = history.slice(-8);
  } catch (error) { if (current===generation) box.querySelector('.answer').textContent = error.message; }
  finally { if (current===generation) { asking=false; updateControls(); $('chatMessages').scrollTop=$('chatMessages').scrollHeight; } }
}
$('analyzeForm').onsubmit = analyze;
$('chatForm').onsubmit = event=>{event.preventDefault();submitQuestion($('questionInput').value);};
$('questionInput').onkeydown = event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();submitQuestion(event.target.value);}};
$('clearSelection').onclick = clearSelection;
$('lod').onchange = ()=>{applyDetail();arrangeVisible();};
$('fitBtn').onclick = ()=>{if(cy)cy.fit(cy.elements().filter(el=>!el.hasClass('filtered')),40);};
$('prevSource').onclick = ()=>{const n=data.nodes.find(n=>n.id===selected);if(sourcePage)loadSource(Math.max(n.lineno,sourcePage.start_line-80));};
$('nextSource').onclick = ()=>{if(sourcePage)loadSource(sourcePage.end_line+1);};
$('symbolSearch').oninput = event=>{
  const query=event.target.value.trim().toLowerCase(); $('searchResults').replaceChildren();
  if (!query || !data) return;
  const matches=data.nodes.filter(n=>n.qualified_name.toLowerCase().includes(query)).slice(0,12);
  if (!matches.length) $('searchResults').append(element('p','No matching symbols','muted'));
  matches.forEach(n=>$('searchResults').append(button(n.qualified_name,()=>{highlight([n.id]);selectNode(n.id);$('searchResults').replaceChildren();})));
};
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>{$('questionInput').value=b.dataset.question;$('questionInput').focus();});

let transcript=[], explorationTicket=0;
let focusSet=null, activeExplore='impact', toastTimer;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4500); }
function showPanel(view) {
  if (view==='inspect'||view==='chat') {
    $('inspector').hidden=view!=='inspect'; $('chatPanel').hidden=view!=='chat';
    $('inspectTab').setAttribute('aria-selected',String(view==='inspect'));
    $('chatTab').setAttribute('aria-selected',String(view==='chat'));
  }
  document.querySelector('main').dataset.mobileView=view;
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if (cy) requestAnimationFrame(()=>cy.resize());
}
function arrangeVisible() {
  if (!cy) return;
  const visible=cy.elements().filter(el=>!el.hasClass('filtered'));
  if (!visible.nodes().length) return;
  const columns=Math.max(1,Math.ceil(Math.sqrt(visible.nodes().length * Math.max(.5,$('cy').clientWidth/Math.max(1,$('cy').clientHeight)))));
  visible.layout({name:'grid',cols:columns,avoidOverlap:true,avoidOverlapPadding:55,nodeDimensionsIncludeLabels:true,spacingFactor:1.15,animate:false,padding:35}).run();
}
function nodeButton(id, suffix='') {
  const b=button(id+suffix,()=>{highlight([id]);selectNode(id);});
  b.title=id; return b;
}
function storageRead(key, fallback) { try{return JSON.parse(localStorage.getItem(key)) ?? fallback;}catch{return fallback;} }
function rememberPath(path) { try{localStorage.setItem('codemap.lastPath',path);}catch{/* optional convenience */} }
function bookmarksKey() {return 'codemap.places.'+data.repository_id;}
function renderBookmarks() {
  if (!data) return;
  const saved=storageRead(bookmarksKey(),[]);
  $('bookmarkCount').textContent=saved.length; $('bookmarks').replaceChildren();
  if (!saved.length) $('bookmarks').append(element('p','Save a selected symbol to return to it later.','muted'));
  saved.forEach(id=>{
    const row=element('div');
    row.append(button(id,()=>{if(data.nodes.some(n=>n.id===id)){highlight([id]);selectNode(id);}else toast('This symbol is no longer present.');}));
    const remove=button('Remove',()=>{
      try{localStorage.setItem(bookmarksKey(),JSON.stringify(saved.filter(x=>x!==id)));renderBookmarks();}catch{toast('Browser storage is unavailable.');}
    }); remove.className='remove-place'; row.append(remove); $('bookmarks').append(row);
  });
}
function populateExplorer() {
  $('repoName').textContent=data.repository_name;
  $('fileMetric').textContent=data.stats.files_parsed; $('symbolMetric').textContent=data.stats.node_count;
  $('entryCount').textContent=data.entrypoints.length; $('entryList').replaceChildren();
  data.entrypoints.forEach(id=>$('entryList').append(nodeButton(id)));
  if (!data.entrypoints.length) $('entryList').append(element('p','No recognized entry points.','muted'));
  $('hotspots').replaceChildren(); (data.overview?.most_called||[]).forEach(n=>$('hotspots').append(nodeButton(n.node_id,` · ${n.callers} callers`)));
  $('cycleCount').textContent=data.overview?.cycle_count||0; $('cycles').replaceChildren();
  (data.overview?.import_cycles||[]).forEach((group,i)=>$('cycles').append(button(`Group ${i+1} · ${group.length} modules`,()=>{focusGraph(group,`Import cycle group ${i+1}`);showPanel('map');})));
  if (!data.overview?.cycle_count) $('cycles').append(element('p','No multi-module import cycles found.','muted'));
  $('diagnostics').replaceChildren(element('p',`${data.stats.unresolved_calls} unresolved calls; ${data.stats.files_failed} failed files. Showing up to 60 diagnostics.`,'muted small'));
  (data.diagnostics||[]).slice(0,60).forEach(d=>$('diagnostics').append(button(`${d.file}:${d.lineno} · ${d.target||d.reason}`,()=>{
    if (d.node_id) {selectNode(d.node_id);loadSource(d.lineno);} else toast(`${d.file}: ${d.reason}`);
  })));
  $('symbolOptions').replaceChildren(); data.nodes.slice(0,3000).forEach(n=>{const o=element('option');o.value=n.id;$('symbolOptions').append(o);});
  renderBookmarks(); $('comparison').replaceChildren();
}
function focusGraph(ids,label,edges=[]) {
  if (!cy) return;
  $('lod').value='all'; $('edgeFilter').value='all'; focusSet=new Set(ids); applyDetail();
  cy.elements().removeClass('evidence');
  cy.nodes().filter(n=>focusSet.has(n.id())).addClass('evidence');
  const edgeKeys=new Set(edges.map(e=>[e.source,e.target,e.type].join('|')));
  cy.edges().filter(e=>edgeKeys.has([e.data('source'),e.data('target'),e.data('etype')].join('|'))).addClass('evidence');
  $('focusLabel').textContent=label; $('focusBar').hidden=false; arrangeVisible();
}
function resetFocus() {
  focusSet=null; $('focusBar').hidden=true; if(cy)cy.elements().removeClass('evidence'); applyDetail();arrangeVisible();
}
function renderRelations(node) {
  $('relations').replaceChildren();
  const edges=data.edges.filter(e=>e.source===node.id||e.target===node.id);
  $('relations').append(element('h3',`Relationships · ${edges.length}`));
  if (!edges.length) $('relations').append(element('p','No resolved relationships. Dynamic dependencies may still exist.','muted'));
  edges.slice(0,60).forEach(e=>{
    const outward=e.source===node.id;
    const label=`${outward?'→':'←'} ${e.type} · ${outward?e.target:e.source}`;
    $('relations').append(button(label,()=>showRelationship(e)));
  });
  if (edges.length>60) $('relations').append(element('p','Showing the first 60 relationships.','muted'));
}
async function showRelationship(edge) {
  if (!data) return;
  const current=generation, ticket=++sourceRequest;
  showPanel('inspect'); $('sourceNavigation').hidden=true; $('sourceHeading').textContent='Loading relationship evidence…';
  try {
    const result=await request('/api/relationship?'+new URLSearchParams({analysis_id:data.analysis_id,source_id:edge.source,target_id:edge.target,relationship:edge.type}));
    if (current!==generation||ticket!==sourceRequest) return;
    $('exploration').hidden=false; $('depthSelect').closest('label').hidden=true; $('exploreTitle').textContent=`${edge.type} evidence`;
    $('exploreResults').replaceChildren(element('p',`${edge.source} → ${edge.target}`,'muted small'));
    result.excerpts.forEach(c=>$('exploreResults').append(button(`${c.file}:${c.start_line}–${c.end_line}`,()=>showSource(c))));
    if (result.excerpts.length) showSource(result.excerpts[0]); else $('sourceHeading').textContent='No source location retained for this relationship.';
    if(result.truncated)$('exploreResults').append(element('p','Showing the first 20 locations.','muted'));
    highlight([edge.source,edge.target]);
  } catch(error) { if(ticket===sourceRequest)toast(error.message); }
}
async function exploreSelection(mode) {
  if (!data||!selected) return;
  activeExplore=mode; $('depthSelect').closest('label').hidden=false;
  const id=selected,current=generation, ticket=++explorationTicket;
  $('exploration').hidden=false; $('exploreTitle').textContent=mode==='impact'?'Potential impact':'Call flow';
  $('exploreResults').textContent='Following relationships…';
  try {
    const result=await request('/api/explore?'+new URLSearchParams({analysis_id:data.analysis_id,node_id:id,mode,depth:$('depthSelect').value}));
    if(current!==generation||ticket!==explorationTicket||id!==selected)return;
    const others=result.nodes.filter(n=>n.id!==id);
    $('exploreResults').replaceChildren(element('p',`${others.length} ${mode==='impact'?'resolved callers':'reachable symbols'}${result.truncated?' · traversal limit reached':''}`,'muted small'));
    others.slice(0,8).forEach(n=>$('exploreResults').append(nodeButton(n.id,` · ${n.distance} hop${n.distance===1?'':'s'}`)));
    if(others.length>8)$('exploreResults').append(nodeGroup(`More callers / symbols · ${others.length-8}`,others.slice(8)));
    if(!others.length)$('exploreResults').append(element('p','No resolved calls in this direction. This does not establish that the symbol is unused.','muted small'));
    if(mode==='impact'){
      const importers=result.importers.nodes.filter(n=>n.file!==data.nodes.find(n=>n.id===id).file);
      $('exploreResults').append(nodeGroup(`File importers · ${importers.length}${result.importers.truncated?' (limited)':''}`,importers));
      $('exploreResults').append(element('p','File imports are separate evidence; they do not prove a call to this symbol.','muted small'));
      if(result.related_tests.length)$('exploreResults').append(nodeGroup('Tests to inspect',result.related_tests));
    }
    $('exploreResults').append(element('p',result.limitation,'muted small'));
    $('exploreResults').append(button('Explain with AI',()=>submitQuestion(mode==='impact'?'Explain potential impact if this selected symbol changes. Distinguish callers from file importers and cite evidence.':'Explain the call flow from this selected symbol, citing the source and unresolved steps.')));
    focusGraph(result.nodes.map(n=>n.id),mode==='impact'?'Potential callers':'Outgoing call flow',result.edges);
  } catch(error){if(ticket===explorationTicket)$('exploreResults').textContent=error.message;}
}
async function loadBaselines() {
  const current=generation;
  try {
    const list=await request('/api/analyses'); if(current!==generation)return;
    $('baselineSelect').replaceChildren();
    list.filter(a=>a.repository_id===data.repository_id&&a.analysis_id!==data.analysis_id).forEach(a=>{
      const option=element('option',new Date(a.created_at).toLocaleTimeString());option.value=a.analysis_id;$('baselineSelect').append(option);
    });
    $('compareBtn').disabled=!$('baselineSelect').options.length;
    if(!$('baselineSelect').options.length){const o=element('option','Re-analyze to compare');o.value='';$('baselineSelect').append(o);}
  }catch(error){toast(error.message);}
}
async function compareSnapshots() {
  if(!data||!$('baselineSelect').value)return;
  const current=generation;
  try{
    const result=await request('/api/compare?'+new URLSearchParams({before_id:$('baselineSelect').value,after_id:data.analysis_id}));
    if(current!==generation)return;
    $('comparison').replaceChildren(element('p',`${result.added.length} added · ${result.changed.length} changed · ${result.removed.length} removed`,'muted'));
    for(const kind of ['added','changed','removed'])result[kind].slice(0,40).forEach(id=>$('comparison').append(kind==='removed'?element('p','− '+id,'muted'):nodeButton(id,` · ${kind}`)));
    $('comparison').append(element('p',`${result.edges_added.length} new relationships · ${result.edges_removed.length} removed. Nested edits also change their enclosing symbols.`,'muted small'));
    if(result.added.length+result.changed.length)focusGraph([...result.added,...result.changed],'Changed symbols');
  }catch(error){toast(error.message);}
}
function download(name,text,type='application/json') {
  const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
async function checkHealth() {
  try {
    const health=await request('/api/health');$('connectionDot').classList.add('online');$('retryBtn').hidden=true;
    if(!analyzing)$('status').textContent=data?`${data.stats.files_parsed} files · Connected`:'Ready to explore';
    $('modelBadge').textContent=health.llm_available?'AI enabled':'Local retrieval';
    $('modelNote').textContent=health.llm_available?`Retrieved source excerpts are sent to ${health.model}.`:'Add your provider key to .env, then restart for AI answers. Source search and citations work now.';
  }catch(error){$('connectionDot').classList.remove('online');$('retryBtn').hidden=false;$('status').textContent='Server offline';$('modelBadge').textContent='Offline';}
}
$('inspectTab').onclick=()=>showPanel('inspect');$('chatTab').onclick=()=>showPanel('chat');
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>showPanel(b.dataset.view));
$('layoutBtn').onclick=arrangeVisible;$('edgeFilter').onchange=applyDetail;
$('zoomIn').onclick=()=>{if(cy)cy.zoom(cy.zoom()*1.25);};$('zoomOut').onclick=()=>{if(cy)cy.zoom(cy.zoom()/1.25);};
$('impactBtn').onclick=()=>exploreSelection('impact');$('flowBtn').onclick=()=>exploreSelection('flow');$('depthSelect').onchange=()=>exploreSelection(activeExplore);
$('clearFocus').onclick=resetFocus;$('resetMap').onclick=()=>{resetFocus();if(data){$('lod').value=data.nodes.length>60?'module':'all';$('edgeFilter').value='all';applyDetail();arrangeVisible();}};
$('refreshBtn').onclick=()=>$('analyzeForm').requestSubmit();$('retryBtn').onclick=checkHealth;
$('saveNode').onclick=()=>{
  if(!selected||!data)return;
  try{const saved=storageRead(bookmarksKey(),[]);localStorage.setItem(bookmarksKey(),JSON.stringify([...new Set([...saved,selected])].slice(-100)));renderBookmarks();toast('Saved in this browser.');}catch{toast('Browser storage is unavailable.');}
};
$('compareBtn').onclick=compareSnapshots;
$('pathBtn').onclick=async()=>{
  if(!data||!selected)return;
  const current=generation,id=selected;
  try{
    const r=await request('/api/path?'+new URLSearchParams({analysis_id:data.analysis_id,source_id:id,target_id:$('targetInput').value.trim()}));
    if(current!==generation||id!==selected)return;
    $('pathResult').replaceChildren();r.path.forEach((nid,i)=>$('pathResult').append(nodeButton(nid,` · step ${i+1}`)));
    if(!r.path.length)$('pathResult').textContent=r.note;else focusGraph(r.path,'Resolved call path');
  }catch(error){if(current===generation)$('pathResult').textContent=error.message;}
};
$('exportGraph').onclick=()=>{if(data)download('codebase-graph.json',JSON.stringify(data,null,2));};
$('exportReport').onclick=()=>{
  if(!data)return;
  const report={repository:data.repository_name,analyzed_at:data.created_at,selected_symbol:selected,stats:data.stats,overview:data.overview,conversation:transcript,limitations:'Static analysis is incomplete. Conversation references refer to the original analysis snapshot.'};
  download('codebase-investigation.json',JSON.stringify(report,null,2));
};
$('clearChat').onclick=()=>{if(asking){toast('Wait for the current answer before clearing.');return;}history=[];transcript=[];showWelcome();};
try{$('pathInput').value=localStorage.getItem('codemap.lastPath')||'';}catch{/* storage is optional */}
checkHealth(); setInterval(checkHealth,15000);
new ResizeObserver(()=>{if(cy)cy.resize();}).observe($('cy'));

function nodeGroup(title,nodes) {
  const details=element('details');details.append(element('summary',title));
  nodes.forEach(n=>details.append(nodeButton(n.id)));return details;
}
function showWelcome() {
  const box=element('div',undefined,'welcome');box.append(element('span','✦','spark'),element('h3','Where do you want to start?'),element('p','Ask a question or select a symbol to focus the conversation.'));
  const prompts=element('div',undefined,'suggestions');
  for(const question of ['Where are the entry points?','Explain the selected symbol and its callers.'])prompts.append(button(question,()=>{$('questionInput').value=question;$('questionInput').focus();}));
  box.append(prompts);$('chatMessages').replaceChildren(box);
}
