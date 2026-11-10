const KEY = "cyberassist-multipage-v1";
const CASES_KEY = "cyberassist-cases-v1";
const ACTIVE_CASE_KEY = "cyberassist-active-case-v1";
const DEMO_AUTH_KEY = "cyberassist-demo-auth-v1";
const DEMO_SCOPE_KEY = "cyberassist-demo-scope-v1";
const DEMO_PROFILES_KEY = "cyberassist-demo-profiles-v1";
const INCIDENT_API_URL = "http://127.0.0.1:8001/incidents";
const EVIDENCE_API_URL = "http://127.0.0.1:8001/evidence";
const INCIDENT_FIELD_MAP = [
  ["incidentType","incident_type"],
  ["incidentDate","incident_date"],
  ["incidentTime","incident_time"],
  ["platform","platform"],
  ["reportedIdentifier","reported_identifier"],
  ["transactionReference","transaction_reference"],
  ["lossAmount","loss_amount"],
  ["location","location"],
  ["summary","summary"],
  ["requestedAction","requested_action"]
];
const $ = id => document.getElementById(id);
let toastTimer;
let savedIncidents = [];
let savedIncidentsLoaded = false;
let savedIncidentsLoading = false;
let savedIncidentsError = "";
let loadingEvidence = false;
let uploadingEvidence = false;
let extractingEvidenceId = null;
let complaintSaveTimer = null;
let workflowLoadToken = 0;
let evidenceLoadToken = 0;
let evidenceError = "";
let evidenceIncidentId = null;

function defaultState(){return {incidentType:"",incidentDate:"",incidentTime:"",platform:"",reportedIdentifier:"",transactionReference:"",lossAmount:"",location:"",summary:"",requestedAction:"",evidence:[],events:[],draft:""}}
function demoScope(){
  let scope=sessionStorage.getItem(DEMO_SCOPE_KEY);
  if(!scope){
    scope=crypto.randomUUID?crypto.randomUUID():"guest-"+Date.now()+"-"+Math.random().toString(16).slice(2);
    sessionStorage.setItem(DEMO_SCOPE_KEY,scope);
  }
  return scope;
}
function scopedKey(key){return key+"::"+demoScope()}
async function selectDemoScope(email){
  if(!crypto.subtle)throw new Error("This browser cannot create a demo workspace profile. Use a modern browser on localhost or HTTPS.");
  const normalized=email.trim().toLowerCase();
  const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(normalized));
  const profileKey=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join("");
  let profiles={};
  try{profiles=JSON.parse(localStorage.getItem(DEMO_PROFILES_KEY)||"{}")}catch(error){throw new Error("Saved demo profiles could not be read.");}
  let scope=profiles[profileKey];
  if(!scope){
    scope=crypto.randomUUID?crypto.randomUUID():"demo-"+Date.now()+"-"+Math.random().toString(16).slice(2);
    profiles[profileKey]=scope;
    localStorage.setItem(DEMO_PROFILES_KEY,JSON.stringify(profiles));
  }
  sessionStorage.setItem(DEMO_SCOPE_KEY,scope);
  return scope;
}
function getCases(){
  try {
    let cases=JSON.parse(localStorage.getItem(scopedKey(CASES_KEY))||"null");
    if(Array.isArray(cases)&&cases.length)return cases;
    const fresh={id:"CA-LOCAL-001",createdAt:new Date().toISOString(),state:defaultState()};
    cases=[fresh];localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),fresh.id);return cases;
  } catch(e) {
    const fresh={id:"CA-LOCAL-001",createdAt:new Date().toISOString(),state:defaultState()};
    localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify([fresh]));localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),fresh.id);return [fresh];
  }
}
function activeCase(){const cases=getCases();let id=localStorage.getItem(scopedKey(ACTIVE_CASE_KEY));let found=cases.find(c=>c.id===id);if(!found){found=cases[0];localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),found.id)}return found}
function caseApiHeaders(caseId=activeCase().id,scope=demoScope()){return {"X-Client-ID":scope,"X-Client-Case-ID":caseId}}
function caseById(caseId){return getCases().find(item=>item.id===caseId)||null}
function setCaseState(caseId,state){
  const cases=getCases(),index=cases.findIndex(item=>item.id===caseId);
  if(index<0)return false;
  cases[index].state={...defaultState(),...state};
  cases[index].updatedAt=new Date().toISOString();
  localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));
  if(localStorage.getItem(scopedKey(ACTIVE_CASE_KEY))===caseId)localStorage.setItem(scopedKey(KEY),JSON.stringify(cases[index].state));
  return true;
}
function activateLocalCase(caseId){
  const selected=caseById(caseId);
  if(!selected)return null;
  workflowLoadToken++;
  evidenceLoadToken++;
  localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),selected.id);
  localStorage.setItem(scopedKey(KEY),JSON.stringify(selected.state||defaultState()));
  return selected;
}
async function apiJson(url,options={}){
  const response=await fetch(url,options);
  let result;
  try{result=await response.json()}catch{throw new Error("The API returned an unreadable response (HTTP "+response.status+").");}
  if(!response.ok)throw new Error(result.detail||"The request failed (HTTP "+response.status+").");
  return result;
}
async function lookupIncidentForCase(caseId){
  const current=caseById(caseId);
  if(!current)return null;
  if(current.backendId)return current.backendId;
  const response=await fetch(INCIDENT_API_URL+"/"+encodeURIComponent(current.id),{headers:caseApiHeaders(current.id)});
  if(response.status===404)return null;
  let incident;
  try{incident=await response.json()}catch{throw new Error("The API returned an unreadable incident response.");}
  if(!response.ok)throw new Error(incident.detail||"The selected incident could not be looked up (HTTP "+response.status+").");
  if(!incident.id)throw new Error("The API did not return an incident ID for the selected case.");
  const cases=getCases(),index=cases.findIndex(item=>item.id===caseId);
  if(index>=0){
    cases[index].backendId=incident.id;
    localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));
  }
  return incident.id;
}
async function lookupActiveIncident(){return lookupIncidentForCase(activeCase().id)}
function getState(){return {...defaultState(),...(activeCase().state||{})}}
function stateFromIncident(incident,state=defaultState()){
  const mapped={...defaultState(),...state};
  INCIDENT_FIELD_MAP.forEach(([local,remote])=>{
    const value=incident[remote];
    mapped[local]=value==null?"":String(value);
  });
  if(mapped.incidentTime.length>5)mapped.incidentTime=mapped.incidentTime.slice(0,5);
  const workflow=incident.workflow_data||{};
  mapped.complainant=workflow.complainant||{};
  mapped.events=(workflow.timeline?.events||[]).map(event=>({
    date:event.date||"",
    date_text:event.date_text||"",
    time:event.time?String(event.time).slice(0,5):"",
    description:event.description||"",
    source:event.source||"User entry",
    evidence_id:event.evidence_id||null,
    evidence_filename:event.evidence_filename||"",
    evidenceName:event.evidence_filename||"",
    origin:event.origin||"user",
    review_status:event.review_status||"candidate"
  }));
  mapped.draft=workflow.complaint?.text||"";
  mapped.complaintNotice=workflow.complaint?.notice||"";
  mapped.serverMissingInformation=workflow.missing_information||null;
  mapped.guidance=workflow.guidance||null;
  return mapped;
}
function incidentPayload(state){
  return Object.fromEntries(INCIDENT_FIELD_MAP.map(([local,remote])=>[remote,state[local]||null]));
}
function getCaseRows(){
  const localCases=getCases();
  const remoteRows=savedIncidents.map(incident=>{
    const local=localCases.find(item=>item.backendId===incident.id);
    const state=stateFromIncident(incident,local?.state||defaultState());
    const caseRecord=local||{
      id:"CA-"+incident.id.slice(0,8).toUpperCase(),
      backendId:incident.id,
      createdAt:incident.created_at,
      state
    };
    return {c:caseRecord,s:state,serverBacked:true,incident};
  });
  const savedIds=new Set(savedIncidents.map(item=>item.id));
  const localRows=localCases
    .filter(item=>!item.backendId||!savedIds.has(item.backendId))
    .map(item=>({c:item,s:{...defaultState(),...(item.state||{})},serverBacked:false,incident:null}));
  return [...remoteRows,...localRows];
}
async function loadSavedIncidents(force=false){
  if(savedIncidentsLoading||(!force&&savedIncidentsLoaded))return;
  const scope=demoScope();
  savedIncidentsLoading=true;
  savedIncidentsError="";
  renderDashboard();
  renderCaseRegister();
  try{
    const response=await fetch(INCIDENT_API_URL,{headers:caseApiHeaders(activeCase().id,scope)});
    if(!response.ok)throw new Error("Backend returned HTTP "+response.status+".");
    const records=await response.json();
    if(!Array.isArray(records))throw new Error("Backend returned an unexpected incident list.");
    if(scope!==demoScope())return;
    savedIncidents=records;
    savedIncidentsLoaded=true;
  }catch(error){
    if(scope!==demoScope())return;
    savedIncidentsError=error instanceof Error?error.message:"Could not load saved incidents.";
    savedIncidentsLoaded=true;
  }finally{
    if(scope!==demoScope())return;
    savedIncidentsLoading=false;
    renderDashboard();
    renderCaseRegister();
  }
}
function activateServerIncident(incident){
  const cases=getCases();
  let local=cases.find(item=>item.backendId===incident.id);
  if(!local){
    const created=createCase(incident.incident_type||"");
    local=getCases().find(item=>item.id===created.id);
  }
  if(!local)return null;
  local.backendId=incident.id;
  local.state=stateFromIncident(incident,local.state||defaultState());
  local.updatedAt=new Date().toISOString();
  localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(getCases()));
  workflowLoadToken++;
  evidenceLoadToken++;
  localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),local.id);
  localStorage.setItem(scopedKey(KEY),JSON.stringify(local.state));
  return local;
}
function setState(state){const cases=getCases(),id=localStorage.getItem(scopedKey(ACTIVE_CASE_KEY)),idx=cases.findIndex(c=>c.id===id);if(idx>=0){cases[idx].state={...defaultState(),...state};cases[idx].updatedAt=new Date().toISOString();localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));}localStorage.setItem(scopedKey(KEY),JSON.stringify(state))}
function createCase(incidentType=""){
  const cases=getCases();const n=cases.length+1;const id="CA-LOCAL-"+String(n).padStart(3,"0");
  const fresh={id,createdAt:new Date().toISOString(),state:{...defaultState(),incidentType}};cases.push(fresh);localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));workflowLoadToken++;evidenceLoadToken++;localStorage.setItem(scopedKey(ACTIVE_CASE_KEY),id);localStorage.setItem(scopedKey(KEY),JSON.stringify(fresh.state));return fresh;
}
function notify(message){const el=$("toast");if(!el)return;el.textContent=message;el.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove("show"),2300)}
function safe(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function hasRecordedValue(value){return value!=null&&String(value).trim()!==""}
function fmtDate(value){if(!value)return "Not provided";const d=new Date(value+"T00:00:00");return Number.isNaN(d.getTime())?value:d.toLocaleDateString("en-IN",{day:"2-digit",month:"short",year:"numeric"})}
function bytes(n){if(!n)return "Manual entry";return n<1024*1024?Math.max(1,Math.round(n/1024))+" KB":(n/1024/1024).toFixed(1)+" MB"}
function formState(){const s=getState();["incidentType","incidentDate","incidentTime","platform","reportedIdentifier","transactionReference","lossAmount","location","summary","requestedAction"].forEach(id=>{if($(id))s[id]=$(id).value.trim()});return s}
function saveForm(){const s=formState();setState(s);return s}
function complaintText(s){
  const recorded=(value)=>value!=null&&String(value).trim()?String(value).trim():"Not provided";
  const reportedLoss=(value)=>{
    const text=String(value??"").trim();
    if(!text)return "Not provided";
    const numeric=text.match(/^(?:(?:INR|Rs\.?|₹)\s*)?([\d,]+(?:\.\d{1,2})?)$/i);
    if(numeric){
      const [whole,fraction]=numeric[1].replace(/,/g,"").split(".");
      return "₹"+Number(whole).toLocaleString("en-IN")+(fraction? "."+fraction:"");
    }
    if(/^(?:INR|Rs\.?)\s*/i.test(text))return text.replace(/^(?:INR|Rs\.?)\s*/i,"₹");
    return text;
  };
  const cell=(value)=>String(value??"").replace(/\s+/g," ").replace(/\\/g,"\\\\").replace(/\|/g,"\\|");
  const evidenceById=new Map((s.evidence||[]).map(item=>[item.evidenceId,item]));
  const lines=[
    "DRAFT COMPLAINT - REVIEW BEFORE SUBMISSION",
    "Prepared from information recorded for this incident. Review all details against original records before submission.",
    "",
    "Subject: "+(s.incidentType?"Report of "+s.incidentType:"Cybercrime incident report"),
    "",
    "COMPLAINANT DETAILS",
    ...[["Name",s.complainant?.name],["Phone",s.complainant?.phone],["Email",s.complainant?.email],["Address",s.complainant?.address]].map(([label,value])=>label+": "+recorded(value)),
    "",
    "INCIDENT NARRATIVE",
    s.summary?.trim()
      ?"Complainant's account as recorded (not independently verified):\n"+s.summary.trim()
      :"Complainant's account: Not provided",
    "",
    "RECORDED INCIDENT DETAILS"
  ];
  const details=[
    ["Incident type",recorded(s.incidentType)],
    ["Date",s.incidentDate?fmtDate(s.incidentDate):"Not provided"],
    ["Time",recorded(s.incidentTime)],
    ["Platform or service",recorded(s.platform)],
    ["Reported contact or profile",recorded(s.reportedIdentifier)],
    ["Transaction reference",recorded(s.transactionReference)],
    ["Reported financial loss",reportedLoss(s.lossAmount)],
    ["Location",recorded(s.location)],
    ["Additional request",recorded(s.requestedAction)]
  ];
  lines.push(...details.map(([label,value])=>"- "+label+": "+value));

  const events=timelineEvents(s)
    .filter(event=>event.description&&String(event.description).trim())
    .sort((a,b)=>((a.date||"9999-99-99")+(a.time||"99:99")).localeCompare((b.date||"9999-99-99")+(b.time||"99:99")));
  lines.push("","CHRONOLOGICAL TIMELINE");
  if(events.length){
    lines.push("| Date & Time | Event Description | Source |","| --- | --- | --- |");
    events.forEach(event=>{
      const when=[event.date?fmtDate(event.date):(event.date_text||"Not provided"),event.time].filter(Boolean).join(" ");
      const evidence=evidenceById.get(event.evidence_id);
      let source=event.origin==="incident"?"Incident Form":event.evidenceName||event.evidence_filename||event.source||"User-provided timeline entry";
      let description=String(event.description).replace(/\s+/g," ").trim();
      const needsVerification=event.origin==="evidence"&&event.review_status!=="reviewed";
      if(needsVerification){
        description="Unverified extracted candidate: "+description;
        if(evidence?.extractionStatus==="ocr_extracted")source+=" - OCR extracted candidate; verify against the original";
        else source+=" - extracted candidate; verify against the original";
      }
      lines.push("| "+[when,description,source].map(value=>cell(value).replace(/[–—]/g,"-")).join(" | ")+" |");
    });
  }else lines.push("No timeline events have been provided for this incident.");

  lines.push("","EVIDENCE PROVIDED");
  if(s.evidence?.length){
    lines.push("","| Evidence File | Evidence Type | Description / Reference |","| --- | --- | --- |");
    s.evidence.forEach(item=>{
      const reference=[item.category,item.evidenceId?"Evidence ID: "+item.evidenceId:""].filter(Boolean).join("; ");
      lines.push("| "+[item.name||"Not provided",item.type||"Not provided",reference||"Not provided"].map(cell).join(" | ")+" |");
    });
  }else lines.push("No evidence has been provided for this incident.");

  const missing=missingRows(s).filter(row=>!row[1]).map(row=>row[0]);
  if(!events.length)missing.push("Timeline");
  lines.push("","MISSING-INFORMATION CHECKLIST");
  if(missing.length)lines.push(...missing.map(item=>"- "+item));
  else lines.push("No missing incident fields were identified by the current checklist.");
  lines.push(
    "",
    "CLOSING",
    "Please review this draft against the original records, correct any inaccuracies, and provide any missing information required by the reporting channel before submission.",
    "",
    "Complainant signature: ______________________________",
    "Date: ______________________________"
  );
  return lines.join("\n");
}
function ensureDraft(s){if(!s.draft)s.draft=complaintText(s);return s}
function renderEvidenceTable(){
  const el=$("evidenceTable");if(!el)return;
  const s=getState();
  $("evidenceSummary").textContent=s.evidence.length+" item"+(s.evidence.length===1?"":"s")+" in this case";
  if(!s.evidence.length){el.innerHTML='<div class="empty-table">No evidence items added. Choose files or add a manual item.</div>';return;}
  el.innerHTML='<table class="data-table evidence-table"><thead><tr><th>FILE / ITEM</th><th>CATEGORY</th><th>FILE TYPE</th><th>SIZE</th><th>UPLOADED</th><th>STATUS</th><th>TEXT EXTRACTION</th><th></th></tr></thead><tbody>'+s.evidence.map((item,index)=>{const extractionStatus=item.extractionStatus||"not_extracted",isImage=/\.(png|jpe?g)$/i.test(item.name),isPdf=/\.pdf$/i.test(item.name),canExtract=item.evidenceId&&(/\.(txt)$/i.test(item.name)||(isPdf&&extractionStatus!=="ocr_extracted")),canOcr=item.evidenceId&&(isImage||(isPdf&&["ocr_required","empty","error"].includes(extractionStatus)))&&!["extracted","ocr_extracted"].includes(extractionStatus);let actions="Not available";if(item.evidenceId&&(canExtract||canOcr)){actions=`<span class="extraction-state ${safe(extractionStatus)}">${safe(extractionStatusLabel(extractionStatus))}</span><div class="evidence-extraction-actions">`;if(canExtract)actions+=`<button class="text-btn" data-extract-text="${safe(item.evidenceId)}" type="button" ${extractingEvidenceId===item.evidenceId?"disabled":""}>${extractionStatus==="not_extracted"?"Extract text":"Re-extract"}</button>`;if(canOcr)actions+=`<button class="text-btn" data-run-ocr="${safe(item.evidenceId)}" type="button" ${extractingEvidenceId===item.evidenceId?"disabled":""}>${extractionStatus==="error"?"Retry OCR":"Run OCR"}</button>`;if(!canOcr&&["extracted","ocr_extracted","empty","error","password_protected","unsupported"].includes(extractionStatus))actions+=`<button class="text-btn" data-view-extraction="${safe(item.evidenceId)}" type="button" ${extractingEvidenceId===item.evidenceId?"disabled":""}>View result</button>`;actions+="</div>";}return `<tr><td><div class="file-name-cell"><span class="compact-icon">${safe((item.name.split(".").pop()||"FILE").slice(0,4).toUpperCase())}</span>${safe(item.name)}</div></td><td>${safe(item.category||"Other")}</td><td>${safe(item.type||"Evidence")}</td><td>${item.evidenceId?formatEvidenceSize(item.size):bytes(item.size)}</td><td>${safe(item.added||"Manual entry")}</td><td><span class="evidence-status${item.evidenceId?" uploaded":""}">${item.evidenceId?"Uploaded to server":"Browser only"}</span></td><td>${actions}</td><td>${item.evidenceId?`<a class="text-btn" href="${EVIDENCE_API_URL}/${encodeURIComponent(item.evidenceId)}/download">Download</a> `:""}<button class="delete-btn" data-delete="${index}" type="button" aria-label="Delete ${safe(item.name)}">Delete</button></td></tr>`}).join("")+'</tbody></table>';
  el.querySelectorAll("[data-delete]").forEach(button=>button.addEventListener("click",()=>deleteEvidenceItem(Number(button.dataset.delete))));
  el.querySelectorAll("[data-extract-text]").forEach(button=>button.addEventListener("click",()=>requestEvidenceText(button.dataset.extractText,true)));
  el.querySelectorAll("[data-run-ocr]").forEach(button=>button.addEventListener("click",()=>requestEvidenceText(button.dataset.runOcr,true,"ocr")));
  el.querySelectorAll("[data-view-extraction]").forEach(button=>button.addEventListener("click",()=>requestEvidenceText(button.dataset.viewExtraction,false)));
  el.querySelectorAll(`a[href^="${EVIDENCE_API_URL}/"]`).forEach(link=>link.addEventListener("click",async event=>{
    if(!link.href.endsWith("/download"))return;
    event.preventDefault();
    try{
      const response=await fetch(link.href,{headers:caseApiHeaders()});
      if(!response.ok)throw new Error("Evidence download failed (HTTP "+response.status+").");
      const blob=await response.blob(),url=URL.createObjectURL(blob),download=document.createElement("a");
      download.href=url;download.download=link.closest("tr")?.querySelector(".file-name-cell")?.textContent?.trim()||"evidence";
      document.body.appendChild(download);download.click();download.remove();URL.revokeObjectURL(url);
    }catch(error){setEvidenceStatus(error instanceof Error?error.message:"Evidence download failed.","error");}
  }));
}
function renderStructuredFacts(item){
  const section=$("structuredFactsPreview"),container=$("structuredFactsContent");
  if(!section||!container)return;
  const facts=item?.structuredData?.facts||[];
  section.hidden=!facts.length;
  container.innerHTML=facts.map(fact=>`<div class="detail-row"><span>${safe(fact.type||"Candidate")}</span><b>${safe(fact.value||"")}</b><small>Source: ${safe(fact.evidence_filename||item.name)} · “${safe(fact.source_excerpt||"")}” · Review against original evidence</small></div>`).join("");
}
function renderExtracted(){
  const el=$("extractedTable");if(!el)return;
  const s=getState();
  const fields=[
    ["Incident type",s.incidentType||"Not provided"],
    ["Date",s.incidentDate?fmtDate(s.incidentDate):"Not provided"],
    ["Approximate time",s.incidentTime||"Not provided"],
    ["Platform / service",s.platform||"Not provided"],
    ["Website, contact, or profile",s.reportedIdentifier||"Not provided"],
    ["Transaction or order reference",s.transactionReference||"Not provided"],
    ["Financial loss",s.lossAmount||"Not provided"],
    ["Location",s.location||"Not provided"],
    ["Incident description",s.summary||"Not provided"],
    ["Additional details or requested action",s.requestedAction||"Not provided"]
  ].map(row=>({field:row[0],value:row[1],source:"Incident form",status:row[1]==="Not provided"?"Not provided":"User-entered"}));
  const evidenceRows=s.evidence.flatMap(item=>{
    const rows=[{field:"Evidence file",value:item.name+" · "+(item.category||"Other"),source:"Evidence inventory",status:"Filename only"}];
    const facts=item.structuredData?.facts||[];
    facts.forEach((fact,index)=>rows.push({
      field:"Extracted "+(fact.review_status==="confirmed"?"fact":"candidate")+" · "+fact.type,
      value:fact.value+" — “"+fact.source_excerpt+"”",
      source:"Extracted from "+(fact.evidence_filename||item.name),
      status:fact.review_status==="confirmed"?"User confirmed":"Candidate · review original",
      evidenceId:item.evidenceId,
      factIndex:index,
      reviewStatus:fact.review_status
    }));
    return rows;
  });
  const rows=[...fields,...evidenceRows];
  el.innerHTML='<table class="data-table"><thead><tr><th>FIELD</th><th>VALUE</th><th>SOURCE</th><th>STATUS</th></tr></thead><tbody>'+rows.map(row=>`<tr><td>${safe(row.field)}</td><td>${safe(row.value)}</td><td>${safe(row.source)}</td><td>${row.evidenceId?`<button class="text-btn" type="button" data-review-fact="${safe(row.evidenceId)}" data-fact-index="${row.factIndex}">${row.reviewStatus==="confirmed"?"Unconfirm":"Confirm"}</button> `:""}<span class="step-tag">${safe(row.status)}</span></td></tr>`).join("")+'</tbody></table>';
  populateAiEvidenceSelector(s.evidence);
  el.querySelectorAll("[data-review-fact]").forEach(button=>button.addEventListener("click",()=>reviewEvidenceFact(button.dataset.reviewFact,Number(button.dataset.factIndex))));
}
function populateAiEvidenceSelector(evidence){
  const select=$("aiEvidenceSelect");if(!select)return;
  const previous=select.value;
  const items=evidence.filter(item=>item.evidenceId&&["extracted","ocr_extracted"].includes(item.extractionStatus));
  select.innerHTML=items.map(item=>`<option value="${safe(item.evidenceId)}">${safe(item.name)}</option>`).join("")||'<option value="">No extracted evidence text available</option>';
  if(items.some(item=>item.evidenceId===previous))select.value=previous;
}
async function reviewEvidenceFact(evidenceId,factIndex){
  const state=getState(),item=state.evidence.find(entry=>entry.evidenceId===evidenceId);
  if(!item||!Array.isArray(item.structuredData?.facts))return;
  const facts=item.structuredData.facts,confirmed=new Set(facts.flatMap((fact,index)=>fact.review_status==="confirmed"?[index]:[]));
  if(confirmed.has(factIndex))confirmed.delete(factIndex);else confirmed.add(factIndex);
  const status=$("aiExtractionStatus");
  if(status){status.textContent="Saving fact review…";status.className="inline-note pending";}
  try{
    const result=await apiJson(EVIDENCE_API_URL+"/"+encodeURIComponent(evidenceId)+"/structured-information",{
      method:"PATCH",
      headers:{"Content-Type":"application/json",...caseApiHeaders()},
      body:JSON.stringify({confirmed_fact_indices:[...confirmed]})
    });
    item.structuredData=result.structured_data;setState(state);refreshShared();
    if(status){status.textContent="Fact review saved. Only confirmed facts can be used in AI complaint drafts.";status.className="inline-note success";}
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"Fact review could not be saved.";status.className="inline-note error";}
  }
}
async function runAiEvidenceExtraction(){
  const status=$("aiExtractionStatus"),evidenceId=$("aiEvidenceSelect")?.value;
  if(!$("aiExtractionConsent")?.checked){
    if(status){status.textContent="Confirm consent before sending evidence text to OpenAI.";status.className="inline-note error";}
    return;
  }
  if(!evidenceId){
    if(status){status.textContent="Select evidence with saved extracted text first.";status.className="inline-note error";}
    return;
  }
  if(status){status.textContent="Sending selected evidence text to OpenAI for candidate extraction…";status.className="inline-note pending";}
  const button=$("runAiExtraction");if(button)button.disabled=true;
  try{
    const result=await apiJson(EVIDENCE_API_URL+"/"+encodeURIComponent(evidenceId)+"/extract-information/ai",{
      method:"POST",
      headers:{"Content-Type":"application/json",...caseApiHeaders()},
      body:JSON.stringify({consent_external_ai:true})
    });
    const state=getState(),item=state.evidence.find(entry=>entry.evidenceId===evidenceId);
    if(item)item.structuredData=result.structured_data;
    setState(state);refreshShared();
    if(status){status.textContent=result.message||"AI candidates extracted. Review each against the original evidence.";status.className="inline-note success";}
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"AI extraction could not be completed.";status.className="inline-note error";}
  }finally{if(button)button.disabled=false;}
}
const financialIncidentTypes=new Set(["Online financial fraud","Online shopping fraud","Phishing","Identity theft","Account compromise"]);
function missingRows(s){
  const rows=[
    ["Incident type",hasRecordedValue(s.incidentType),"Choose a category if you can identify one."],
    ["Incident date",hasRecordedValue(s.incidentDate),"Add the date if it is known; an approximate date is okay."],
    ["Incident time",hasRecordedValue(s.incidentTime),"Add the time if it is known."],
    ["Incident description",!!(s.summary||"").trim(),"Describe what happened in your own words."],
    ["Platform or service",hasRecordedValue(s.platform),"Name the app, website, bank, or service if known."],
    ["Reported contact or profile",hasRecordedValue(s.reportedIdentifier),"Add a URL, phone number, email, or profile name if relevant."],
    ["Incident location",hasRecordedValue(s.location),"Add the relevant city, state, or location if known."]
  ];
  if(financialIncidentTypes.has(s.incidentType)||s.incidentType==="Other"||hasRecordedValue(s.lossAmount)||hasRecordedValue(s.transactionReference)){
    rows.push(["Financial loss",hasRecordedValue(s.lossAmount),"If money was lost, add the amount if known."],["Transaction or order reference",hasRecordedValue(s.transactionReference),"Add a UTR, transaction ID, or order number if available."]);
  }
  rows.push(["Supporting evidence",s.evidence.length>0,"List relevant files or records if they are available."]);
  const complainant=s.complainant||{};
  rows.push(["Complainant details",[complainant.name,complainant.phone,complainant.email,complainant.address].some(hasRecordedValue),"Add complainant details if they are available and required by the reporting channel."]);
  return rows;
}
function renderMissing(){
  const el=$("missingChecks");if(!el)return;
  const state=getState(),serverItems=state.serverMissingInformation?.items;
  const rows=serverItems
    ?serverItems.map(item=>[item.field,item.status==="recorded",item.prompt])
    :missingRows(state);
  const missing=rows.filter(row=>!row[1]).length,complete=rows.length-missing;
  $("missingPendingCount").textContent=missing;
  $("missingCompletedCount").textContent=complete;
  $("missingBadge").textContent=missing?missing+" prompt"+(missing===1?"":"s")+" to review":"All current prompts have information";
  const groups=[
    ["Incident basics",["Incident type","Incident date","Incident time","Incident description","Incident location"]],
    ["Contact and transaction details",["Platform or service","Reported contact or profile","Financial loss","Transaction or order reference","Complainant details"]],
    ["Supporting records",["Supporting evidence"]]
  ];
  el.innerHTML=groups.map(([title,labels])=>{
    const items=rows.filter(row=>labels.includes(row[0]));
    if(!items.length)return "";
    const done=items.filter(row=>row[1]).length;
    return `<section class="missing-group"><div class="missing-group-heading"><h4>${title}</h4><span>${done} of ${items.length} recorded</span></div><div class="missing-group-items">${items.map(row=>`<div class="missing-row ${row[1]?"done":""}"><span class="missing-mark" aria-hidden="true">${row[1]?"✓":"!"}</span><div><b>${safe(row[0])}</b><p>${row[1]?"Information recorded.":safe(row[2])}</p></div><span class="missing-state">${row[1]?"Recorded":"To review"}</span></div>`).join("")}</div></section>`;
  }).join("");
}
function timelineEvents(s){
  const events=(s.events||[]).map((event,index)=>({
    ...event,
    evidenceName:event.evidenceName||event.evidence_filename||"",
    sourceIndex:index,
    title:event.title|| (event.origin==="evidence"?"Evidence-derived event (review)":event.origin==="incident"?"Incident reported by user":"User-added event"),
    userAdded:event.origin!=="incident"
  }));
  if((s.summary||"").trim()&&!events.some(event=>event.origin==="incident"))events.unshift({date:s.incidentDate||"",time:s.incidentTime||"",title:"Incident described by user",description:s.summary,source:"Incident form",origin:"incident",userAdded:false});
  return events;
}
function finishTimelineEdit(){
  const form=$("timelineForm");
  if(form){form.reset();delete form.dataset.editIndex;}
  const panel=$("eventFormPanel"),title=$("timelineFormTitle"),submit=$("saveTimelineEvent"),add=$("addTimelineEvent");
  if(panel)panel.hidden=true;
  if(title)title.textContent="Add timeline event";
  if(submit)submit.textContent="Add event";
  if(add)add.focus();
}
function startTimelineEdit(index){
  const state=getState(),event=state.events[index],form=$("timelineForm");if(!event||!form)return;
  form.dataset.editIndex=String(index);
  $("eventDate").value=event.date||"";$("eventTime").value=event.time||"";$("eventDescription").value=event.description||"";
  const evidenceIndex=state.evidence.findIndex(item=>item.name===event.evidenceName);
  $("eventEvidenceSource").value=evidenceIndex<0?"":String(evidenceIndex);
  $("eventFormPanel").hidden=false;$("timelineFormTitle").textContent="Edit timeline event";$("saveTimelineEvent").textContent="Save changes";$("eventDescription").focus();
}
function renderTimeline(){
  const el=$("timelineEvents");if(!el)return;
  const s=getState(),events=timelineEvents(s).sort((a,b)=>((a.date||"9999-99-99")+(a.time||"")).localeCompare((b.date||"9999-99-99")+(b.time||"")));
  const sourceSelect=$("eventEvidenceSource");
  if(sourceSelect){const old=sourceSelect.value;sourceSelect.innerHTML='<option value="">No evidence linked</option>'+s.evidence.map((item,index)=>`<option value="${index}">${safe(item.name)}</option>`).join("");if(old!==undefined)sourceSelect.value=old;}
  if(!events.length){el.innerHTML='<div class="empty-table">No events recorded yet. Add an event; leave its date blank if it is unknown.</div>';return;}
  el.innerHTML=events.map(event=>`<div class="timeline-event"><span class="timeline-date">${safe(event.date?fmtDate(event.date):(event.date_text||"Date not provided"))}${event.time?"<br>"+safe(event.time):""}</span><span class="timeline-rail"><i class="timeline-dot"></i></span><div class="timeline-body"><b>${safe(event.title||"Incident event")}</b><p>${safe(event.description||"")}</p><p>Source: ${safe(event.source||"User entry")}${event.review_status==="candidate"?" · Review required":""}</p>${event.evidenceName?`<p class="event-evidence-link">Evidence: <strong>${safe(event.evidenceName)}</strong></p>`:""}${event.userAdded?`<div class="timeline-event-actions"><button class="text-btn" type="button" data-edit-event="${event.sourceIndex}">Edit</button><button class="text-btn timeline-remove" type="button" data-remove-event="${event.sourceIndex}">Remove</button></div>`:`<a class="text-btn timeline-edit-source" href="incident.html">Edit incident details</a>`}</div></div>`).join("");
  el.querySelectorAll("[data-edit-event]").forEach(button=>button.addEventListener("click",()=>startTimelineEdit(Number(button.dataset.editEvent))));
  el.querySelectorAll("[data-remove-event]").forEach(button=>button.addEventListener("click",async()=>{
    const state=getState();state.events.splice(Number(button.dataset.removeEvent),1);setState(state);refreshShared();
    logActivity("timeline","Timeline event removed","A user-added timeline event was removed.","updated");notify("Timeline event removed.");
    try{await saveTimelineToApi(state.events);if($("timelineStatus")){$("timelineStatus").textContent="Timeline change saved to the database."; $("timelineStatus").className="inline-note success";}}
    catch(error){if($("timelineStatus")){$("timelineStatus").textContent=error instanceof Error?error.message:"Timeline change could not be saved."; $("timelineStatus").className="inline-note error";}}
  }));
}
function renderProcessing(){
  const list=$("processSteps");if(!list)return;
  const state=getState(),count=state.evidence.length,states=list.querySelectorAll(".process-state"),statuses=[
    state.evidence.some(item=>["extracted","ocr_extracted"].includes(item.extractionStatus))?"Text/OCR extracted":"PDF/TXT and image OCR",
    state.evidence.some(item=>item.structuredData?.facts?.length)?"Candidate facts available":"Local extraction available",
    timelineEvents(state).length?"Timeline candidates available":"Generate or add events"
  ];
  if(states[0]){states[0].textContent=count?count+" item(s) listed":"No evidence items";states[0].classList.toggle("complete",count>0);}
  states.forEach((el,index)=>{if(index>0){el.textContent=statuses[index-1]||"Not connected";el.classList.remove("complete");}});
  const status=$("processStatus");if(status)status.textContent=count?"Inventory ready":"No evidence";
}
function detailRows(s){return [["Incident type",s.incidentType||"Not provided"],["Date",fmtDate(s.incidentDate)],["Time",s.incidentTime||"Not provided"],["Platform",s.platform||"Not provided"],["Reported contact or profile",s.reportedIdentifier||"Not provided"],["Transaction reference",s.transactionReference||"Not provided"],["Financial loss",s.lossAmount||"Not provided"],["Location",s.location||"Not provided"]];}
function renderDashboard(){
  const s=getState(),evidence=$("statEvidence");if(!evidence)return;
  const events=timelineEvents(s),missing=(s.serverMissingInformation?.items||missingRows(s)).filter(item=>item.status==="needs_review"||!item[1]).length;
  const stages=[!!(s.summary||"").trim(),s.evidence.length>0,events.length>0,!!(s.draft||s.summary)];
  const completedStages=stages.filter(Boolean).length;
  const caseRows=getCaseRows();
  $("statCases").textContent=caseRows.length;
  evidence.textContent=s.evidence.length;$("statTimeline").textContent=events.length;$("statMissing").textContent=missing;
  $("statProgress").textContent=Math.round(completedStages/stages.length*100)+"%";
  $("statProgressDetail").textContent=completedStages+" of "+stages.length+" stages with information";
  $("dashboardCase").innerHTML=detailRows(s).map(row=>`<div class="detail-row"><span>${safe(row[0])}</span><b>${safe(row[1])}</b></div>`).join("");
  const steps=[["Incident details",!!(s.summary||"").trim(),"incident.html"],["Evidence inventory",s.evidence.length>0,"evidence.html"],["Timeline review",events.length>0,"timeline.html"],["Complaint draft",!!s.draft||!!s.summary,"complaint.html"]];
  $("progressList").innerHTML=steps.map(step=>`<a class="progress-row" href="${step[2]}"><span class="progress-icon ${step[1]?"done":""}">${step[1]?"✓":"—"}</span><span><b>${step[0]}</b><small>${step[1]?"Information available":"Not started"}</small></span><span class="progress-state">${step[1]?"Review":"Open"} →</span></a>`).join("");
  $("dashboardEvidence").innerHTML=s.evidence.length?s.evidence.slice(-4).reverse().map(item=>`<div class="compact-row"><span class="compact-icon">${safe((item.name.split(".").pop()||"FILE").slice(0,4).toUpperCase())}</span><div><b>${safe(item.name)}</b><small>${safe(item.category||"Other")} · ${safe(item.type||"Evidence")} · ${bytes(item.size)}</small></div></div>`).join(""):'<div class="empty-table">No evidence added yet.</div>';
  $("dashboardDraft").textContent=(s.draft||complaintText(s)).slice(0,360);
  const caseHost=$("dashboardRecentCases");
  if(caseHost){
    const activeId=localStorage.getItem(scopedKey(ACTIVE_CASE_KEY)),recent=caseRows.slice(0,4);
    caseHost.innerHTML=recent.map(row=>{
      const active=row.c.id===activeId;
      const status=active?"Active case":row.s.summary||row.s.incidentType?"In progress":"Not started";
      return `<button class="recent-case" type="button" data-dashboard-case="${safe(row.c.id)}" data-backend-id="${safe(row.c.backendId||"")}"><span class="recent-case-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M6 3.75h8l4 4v12.5H6z"/><path d="M14 3.75v4h4M9 12h6M9 15.5h6"/></svg></span><span class="recent-case-copy"><b>${safe(row.s.incidentType||"Incident type not specified")}</b><small>${safe(row.c.id)} · ${safe(fmtDate(row.s.incidentDate))}</small></span><span class="recent-case-status ${active?"active":""}">${status}</span></button>`;
    }).join("")||'<div class="empty-table">No incident cases found.</div>';
    caseHost.querySelectorAll("[data-dashboard-case]").forEach(button=>button.addEventListener("click",()=>{
      const selected=getCases().find(item=>item.id===button.dataset.dashboardCase);
      const incident=savedIncidents.find(item=>item.id===button.dataset.backendId);
      if(incident)activateServerIncident(incident);
      else if(selected)activateLocalCase(selected.id);
      else return;
      refreshShared();
      loadCurrentWorkflow();
    }));
    const status=$("dashboardCasesStatus");
    if(status){
      status.classList.toggle("error",!!savedIncidentsError);
      status.classList.toggle("pending",savedIncidentsLoading&&!savedIncidentsError);
      status.textContent=savedIncidentsError
        ?"Database cases could not be loaded ("+savedIncidentsError+"). Showing browser-saved cases where available."
        :savedIncidentsLoading?"Loading saved cases from the database…"
        :"Showing database cases and browser-only cases.";
    }
  }
}
function renderDraftPage(){const el=$("complaintEditor");if(!el)return;const s=ensureDraft(getState());setState(s);el.value=s.draft;$("draftFacts").innerHTML=detailRows(s).map(r=>`<div class="detail-row"><span>${safe(r[0])}</span><b>${safe(r[1])}</b></div>`).join("")}
function renderReview(){
  const el=$("reviewFacts");if(!el)return;
  const s=ensureDraft(getState());setState(s);
  el.innerHTML=detailRows(s).map(row=>`<div class="detail-row"><span>${safe(row[0])}</span><b>${safe(row[1])}</b></div>`).join("");
  $("reviewEvidence").innerHTML=s.evidence.length?s.evidence.map(item=>`<div class="compact-row"><span class="compact-icon">${safe((item.name.split(".").pop()||"FILE").slice(0,4).toUpperCase())}</span><div><b>${safe(item.name)}</b><small>${safe(item.category||"Other")} · ${safe(item.type||"Evidence")}</small></div></div>`).join(""):'<div class="empty-table">No evidence listed.</div>';
  $("reviewDraft").textContent=s.draft;
}
function refreshShared(){renderEvidenceTable();renderDashboard();renderMissing();renderTimeline();renderExtracted();renderDraftPage();renderReview();renderProcessing();}
function exportDraft(text){const blob=new Blob([text],{type:"text/plain;charset=utf-8"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="cyber-assist-complaint-draft.txt";document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);logActivity("draft","Complaint draft exported","A text copy of the complaint draft was downloaded.","completed");notify("Complaint draft exported.")}

const incidentForm=$("incidentForm");
if(incidentForm){
  const fieldIds=["incidentType","incidentDate","incidentTime","platform","reportedIdentifier","transactionReference","lossAmount","location","summary","requestedAction"];
  const s=getState();
  fieldIds.forEach(id=>{if($(id))$(id).value=s[id]||""});
  let submitting=false;
  const submitButton=incidentForm.querySelector('button[type="submit"]');
  const saveStatus=$("incidentSaved");
  incidentForm.addEventListener("submit",async e=>{
    e.preventDefault();
    if(submitting)return;
    submitting=true;
    if(submitButton){submitButton.disabled=true;submitButton.textContent="Saving…"}
    const st=saveForm();
    setState(st);
    refreshShared();
    logActivity("incident","Incident details saved locally","Incident details were updated in this browser.","completed");
    if(saveStatus){
      saveStatus.textContent="Saved in this browser. Sending to the backend…";
      saveStatus.classList.remove("error","success");
      saveStatus.classList.add("pending");
    }
    try{
      const backendId=activeCase().backendId;
      const response=await fetch(backendId?INCIDENT_API_URL+"/"+encodeURIComponent(backendId):INCIDENT_API_URL,{
        method:backendId?"PUT":"POST",
        headers:{"Content-Type":"application/json",...caseApiHeaders(activeCase().id)},
        body:JSON.stringify(incidentPayload(st))
      });
      if(!response.ok){
        const message=response.status===422
          ?"The backend rejected one or more incident fields (HTTP 422). Your details remain saved in this browser; check the date and time fields, then retry."
          :`Backend returned HTTP ${response.status}. Your details remain saved in this browser; please retry.`;
        if(saveStatus){
          saveStatus.textContent=message;
          saveStatus.classList.remove("pending","success");
          saveStatus.classList.add("error");
        }
        notify("Backend could not save the incident (HTTP "+response.status+").");
        return;
      }
      const result=await response.json();
      const savedIncident=result.incident;
      if(!savedIncident?.id)throw new Error("The backend response did not include a saved incident ID.");
      const cases=getCases(),current=cases.find(item=>item.id===localStorage.getItem(scopedKey(ACTIVE_CASE_KEY)));
      if(current){
        current.backendId=savedIncident.id;
        localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));
      }
      const savedIndex=savedIncidents.findIndex(item=>item.id===savedIncident.id);
      if(savedIndex>=0)savedIncidents[savedIndex]=savedIncident;
      else savedIncidents.unshift(savedIncident);
      savedIncidentsLoaded=true;
      savedIncidentsError="";
      if(saveStatus){
        saveStatus.textContent=backendId
          ?"Incident details updated in the browser and database."
          :"Incident details saved in the browser and database.";
        saveStatus.classList.remove("pending","error");
        saveStatus.classList.add("success");
      }
      refreshShared();
      notify("Incident details saved successfully.");
    }catch(error){
      const message=error instanceof TypeError
        ?"Could not reach the backend at http://127.0.0.1:8001. Check that the API is running and allows this frontend origin. Your details remain saved in this browser; please retry."
        :error.message+" Your details remain saved in this browser.";
      if(saveStatus){
        saveStatus.textContent=message;
        saveStatus.classList.remove("pending","success");
        saveStatus.classList.add("error");
      }
      notify("Backend unavailable. Your details remain saved in this browser.");
    }finally{
      submitting=false;
      if(saveStatus)saveStatus.classList.remove("pending");
      if(submitButton){submitButton.disabled=false;submitButton.textContent="Save incident details"}
    }
  });
  incidentForm.querySelectorAll("input,select,textarea").forEach(el=>el.addEventListener("change",()=>{
    const state=saveForm();
    setState(state);
    if(saveStatus)saveStatus.textContent="Changes are stored in this browser.";
  }));
}
function formatEvidenceSize(size){
  if(size<1024)return size+" B";
  if(size<1024*1024)return (size/1024).toFixed(1)+" KB";
  return (size/1024/1024).toFixed(1)+" MB";
}
function setEvidenceStatus(message,kind=""){
  const status=$("evidenceStatus");if(!status)return;
  status.textContent=message;
  status.classList.remove("pending","success","error");
  if(kind)status.classList.add(kind);
}
function extractionStatusLabel(status){
  return ({
    not_extracted:"Not extracted",
    extracted:"Text extracted",
    ocr_required:"OCR required",
    ocr_extracted:"OCR text extracted",
    empty:"No readable text",
    password_protected:"Password protected",
    unsupported:"Unsupported file",
    error:"Extraction error"
  })[status]||"Unknown status";
}
function evidenceFromApi(item){
  return {
    evidenceId:item.id,
    incidentId:item.incident_id,
    name:item.original_filename,
    category:item.category,
    type:item.file_type,
    size:item.file_size,
    added:new Date(item.uploaded_at).toLocaleDateString("en-IN"),
    uploadedAt:item.uploaded_at,
    extractionStatus:item.extraction_status||"not_extracted",
    extractionMessage:item.extraction_message||"",
    extractedAt:item.extracted_at||null,
    structuredData:item.structured_data||null
  };
}
function mergeEvidenceRecords(records,caseId=activeCase().id){
  const selected=caseById(caseId);
  if(!selected)return;
  const state={...defaultState(),...(selected.state||{})};
  const localItems=state.evidence.filter(item=>!item.evidenceId);
  state.evidence=[...records.map(evidenceFromApi),...localItems];
  setCaseState(caseId,state);
  if(activeCase().id===caseId)refreshShared();
}
async function loadEvidenceInventory(){
  const current=activeCase(),caseId=current.id,incidentStatus=$("evidenceIncidentStatus");
  const loadToken=++evidenceLoadToken;
  const isCurrent=()=>loadToken===evidenceLoadToken&&activeCase().id===caseId;
  evidenceIncidentId=current.backendId||null;
  loadingEvidence=true;
  evidenceError="";
  if(incidentStatus){
    incidentStatus.textContent="Loading evidence for selected incident…";
    incidentStatus.classList.remove("error");
    incidentStatus.classList.add("pending");
  }
  try{
    if(!evidenceIncidentId){
      const backendId=await lookupIncidentForCase(caseId);
      if(!isCurrent())return;
      if(!backendId){
        if(incidentStatus){
          incidentStatus.textContent="Selected case "+current.id+" is stored in this browser. Uploading a file will create its database incident record.";
          incidentStatus.classList.remove("error","pending");
        }
        return;
      }
      evidenceIncidentId=backendId;
    }
    const response=await fetch(INCIDENT_API_URL+"/"+encodeURIComponent(evidenceIncidentId)+"/evidence",{headers:caseApiHeaders(caseId)});
    if(!isCurrent())return;
    if(!response.ok){
      const error=await response.json();
      throw new Error(error.detail||"Evidence could not be loaded (HTTP "+response.status+").");
    }
    const records=await response.json();
    mergeEvidenceRecords(records,caseId);
    if(incidentStatus){
      incidentStatus.textContent="Evidence is linked to selected incident "+current.id+".";
      incidentStatus.classList.remove("error","pending");
    }
  }catch(error){
    if(!isCurrent())return;
    evidenceError=error instanceof Error?error.message:"Evidence could not be loaded.";
    if(incidentStatus){
      incidentStatus.textContent=evidenceError;
      incidentStatus.classList.remove("pending");
      incidentStatus.classList.add("error");
    }
    setEvidenceStatus("Could not load uploaded evidence. Check the API connection and retry by refreshing this page.","error");
  }finally{
    if(isCurrent()){
      loadingEvidence=false;
      renderEvidenceTable();
    }
  }
}
async function ensureIncidentIdForCase(caseId){
  const current=caseById(caseId);
  if(!current)throw new Error("The selected case is no longer available.");
  const existingId=await lookupIncidentForCase(caseId);
  const incident=existingId
    ?(await apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(existingId),{
      method:"PUT",
      headers:{"Content-Type":"application/json",...caseApiHeaders(caseId)},
      body:JSON.stringify(incidentPayload(current.state||defaultState()))
    })).incident
    :(await apiJson(INCIDENT_API_URL,{
      method:"POST",
      headers:{"Content-Type":"application/json",...caseApiHeaders(caseId)},
      body:JSON.stringify(incidentPayload(current.state||defaultState()))
    })).incident;
  if(!incident?.id)throw new Error("The API did not return an incident ID for the selected case.");
  current.backendId=incident.id;
  const cases=getCases();
  const index=cases.findIndex(item=>item.id===current.id);
  if(index>=0)cases[index].backendId=incident.id;
  localStorage.setItem(scopedKey(CASES_KEY),JSON.stringify(cases));
  savedIncidents=[incident,...savedIncidents.filter(item=>item.id!==incident.id)];
  savedIncidentsLoaded=true;
  return incident.id;
}
async function ensureActiveIncidentId(){return ensureIncidentIdForCase(activeCase().id)}
async function saveTimelineToApi(events){
  const caseId=activeCase().id;
  const incidentId=await ensureIncidentIdForCase(caseId);
  const payload=events.map(event=>({
    date:event.date||null,
    date_text:event.date_text||null,
    time:event.time||null,
    description:event.description,
    source:event.source||"User entry",
    evidence_id:event.evidence_id||null,
    evidence_filename:event.evidenceName||event.evidence_filename||null,
    origin:event.origin||"user",
    review_status:event.review_status||null
  }));
  return apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/timeline",{
    method:"PUT",
    headers:{"Content-Type":"application/json",...caseApiHeaders(caseId)},
    body:JSON.stringify({events:payload})
  });
}
async function generateTimelineFromApi(){
  const caseId=activeCase().id;
  const status=$("timelineStatus");
  if(status){status.textContent="Generating timeline candidates from saved evidence…";status.className="inline-note pending";}
  try{
    const incidentId=await ensureIncidentIdForCase(caseId);
    const result=await apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/generate-timeline",{
      method:"POST",headers:caseApiHeaders(caseId)
    });
    if(activeCase().id!==caseId)return;
    const state={...defaultState(),...(caseById(caseId)?.state||{})};
    state.events=(result.events||[]).map(event=>({
      date:event.date||"",
      date_text:event.date_text||"",
      time:event.time?String(event.time).slice(0,5):"",
      description:event.description||"",
      source:event.source||"Evidence",
      evidence_id:event.evidence_id||null,
      evidence_filename:event.evidence_filename||"",
      evidenceName:event.evidence_filename||"",
      origin:event.origin||"evidence",
      review_status:event.review_status||"candidate"
    }));
    setCaseState(caseId,state);refreshShared();
    if(status){status.textContent=result.notice||"Timeline generated. Review evidence-derived events and dates.";status.className="inline-note success";}
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"Timeline candidates could not be generated.";status.className="inline-note error";}
  }
}
async function refreshMissingInformation(){
  const caseId=activeCase().id;
  const status=$("missingStatus");
  if(status){status.textContent="Checking saved incident details and evidence…";status.className="inline-note pending";}
  try{
    const incidentId=await ensureIncidentIdForCase(caseId);
    const result=await apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/missing-information",{
      headers:caseApiHeaders(caseId)
    });
    if(activeCase().id!==caseId)return;
    const state={...defaultState(),...(caseById(caseId)?.state||{})};state.serverMissingInformation=result;
    setCaseState(caseId,state);refreshShared();
    if(status){status.textContent="Checklist updated from the incident and evidence currently saved.";status.className="inline-note success";}
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"Missing-information checklist could not be refreshed.";status.className="inline-note error";}
  }
}
async function generateComplaintFromApi(){
  clearTimeout(complaintSaveTimer);
  const caseId=activeCase().id;
  const status=$("complaintStatus");
  if(status){status.textContent="Generating a fact-based structured draft…";status.className="inline-note pending";}
  try{
    const incidentId=await ensureIncidentIdForCase(caseId);
    const consent=$("complaintAiConsent")?.checked===true;
    const options={method:"POST",headers:caseApiHeaders(caseId)};
    if(consent){
      options.headers["Content-Type"]="application/json";
      options.body=JSON.stringify({consent_external_ai:true});
    }
    const result=await apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/generate-complaint",options);
    if(activeCase().id!==caseId)return;
    const state={...defaultState(),...(caseById(caseId)?.state||{})};
    state.draft=result.text||"";state.complaintNotice=result.notice||"";state.draftMethod=result.method||"local_template";setCaseState(caseId,state);
    refreshShared();
    if(status){status.textContent=result.notice||"Structured draft generated. Review every statement before use.";status.className="inline-note success";}
  }catch(error){
    if(status&&activeCase().id===caseId){status.textContent=error instanceof Error?error.message:"Complaint draft could not be generated.";status.className="inline-note error";}
  }
}
async function saveComplaintToApi(text,reviewed=false,caseId=activeCase().id){
  const incidentId=await ensureIncidentIdForCase(caseId);
  return apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/complaint",{
    method:"PUT",
    headers:{"Content-Type":"application/json",...caseApiHeaders(caseId)},
    body:JSON.stringify({text,reviewed})
  });
}
async function downloadComplaint(format){
  const caseId=activeCase().id,state=caseById(caseId)?.state||defaultState();
  const text=$("complaintEditor")?.value??state.draft;
  setCaseState(caseId,{...state,draft:text});
  const status=$("complaintStatus");
  if(status){status.textContent="Saving the draft before export…";status.className="inline-note pending";}
  try{
    await saveComplaintToApi(text,false,caseId);
    const selected=caseById(caseId);
    if(!selected?.backendId)throw new Error("The selected case has no linked database incident.");
    const incidentId=selected.backendId;
    const response=await fetch(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/complaint/export?format="+encodeURIComponent(format),{
      headers:caseApiHeaders(caseId)
    });
    if(!response.ok){
      let result={};try{result=await response.json()}catch{}
      throw new Error(result.detail||"Complaint export failed (HTTP "+response.status+").");
    }
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download="cyber-assist-complaint."+(format==="pdf"?"pdf":"txt");
    document.body.appendChild(link);link.click();link.remove();URL.revokeObjectURL(url);
    if(status&&activeCase().id===caseId){status.textContent="Saved complaint exported as "+format.toUpperCase()+".";status.className="inline-note success";}
    if(activeCase().id===caseId)logActivity("draft","Complaint draft exported","The saved complaint draft was downloaded as "+format.toUpperCase()+".","completed");
  }catch(error){
    if(status&&activeCase().id===caseId){status.textContent=error instanceof Error?error.message:"Complaint export could not be completed.";status.className="inline-note error";}
  }
}
async function searchGuidance(query){
  const caseId=activeCase().id;
  const status=$("guidanceStatus"),resultsEl=$("guidanceResults");
  if(status){status.textContent="Searching the local guidance collection…";status.className="inline-note pending";}
  if(resultsEl)resultsEl.replaceChildren();
  try{
    const incidentId=await ensureIncidentIdForCase(caseId);
    const result=await apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/guidance",{
      method:"POST",
      headers:{"Content-Type":"application/json",...caseApiHeaders(caseId)},
      body:JSON.stringify({query})
    });
    if(activeCase().id!==caseId)return;
    const state={...defaultState(),...(caseById(caseId)?.state||{})};state.guidance=result;setCaseState(caseId,state);
    renderGuidanceResults(result);
    if(status){status.textContent=(result.results||[]).length+" source passage(s) retrieved from the local index. These are source excerpts, not application-generated legal guidance; verify current instructions with the official publisher.";status.className="inline-note success";}
  }catch(error){
    if(status&&activeCase().id===caseId){status.textContent=error instanceof Error?error.message:"Guidance search could not be completed.";status.className="inline-note error";}
  }
}
function renderGuidanceResults(result){
  const resultsEl=$("guidanceResults");
  if(!resultsEl)return;
  resultsEl.innerHTML=(result.results||[]).map(item=>{
    const usReference=item.jurisdiction==="United States";
    const sourceLabel=usReference
      ?"US-specific application summary — not Indian reporting procedure"
      :item.source_type==="official_document"
        ?"Official source document"
        :item.source_type==="application_summary"
          ?"Application-generated summary"
          :"Official source excerpt";
    const details=[
      sourceLabel,
      item.jurisdiction||"Jurisdiction unspecified",
      item.publisher||item.issuing_authority||"",
      item.publication_date?"Published "+item.publication_date:"",
      item.reference||"Source location unavailable"
    ].filter(Boolean).map(safe).join(" · ");
    return `<article class="guidance-result"><h4>${safe(item.source_title||item.source_name||item.document_name||"Guidance source")}</h4><p>${safe(item.text)}</p><small>${details}<br><a href="${safe(item.source_url||"#")}" target="_blank" rel="noopener noreferrer">Source: ${safe(item.source_url||"Unavailable")}</a></small></article>`;
  }).join("")||'<p class="empty-table">No relevant local guidance matched. Try different terms.</p>';
}
async function loadKnowledgeBaseStatus(){
  const status=$("knowledgeBaseStatus");if(!status)return;
  try{
    const result=await apiJson("http://127.0.0.1:8001/knowledge-base/status");
    status.textContent=result.ready
      ?`${result.chunks} chunks · ${result.documents.length} source document(s)`
      :"Knowledge base empty · ingest official PDF/TXT sources";
    status.classList.toggle("error",!result.ready);
  }catch(error){
    status.textContent=error instanceof Error?error.message:"Knowledge-base status unavailable.";
    status.classList.add("error");
  }
}
async function loadCurrentWorkflow(){
  const hasTimeline=!!$("timelineEvents"),hasMissing=!!$("missingChecks"),hasComplaint=!!$("complaintEditor"),hasExtracted=!!$("extractedTable"),hasGuidance=!!$("guidanceResults");
  if(!hasTimeline&&!hasMissing&&!hasComplaint&&!hasExtracted&&!hasGuidance)return;
  const caseId=activeCase().id,loadToken=++workflowLoadToken;
  const isCurrent=()=>loadToken===workflowLoadToken&&activeCase().id===caseId;
  const extractedStatus=$("extractedSyncStatus");
  if(extractedStatus){extractedStatus.textContent="Loading saved evidence and extracted candidates…";extractedStatus.className="inline-note pending";}
  try{
    const incidentId=await lookupIncidentForCase(caseId);
    if(!isCurrent())return;
    if(!incidentId){
      if(hasMissing&&$("missingStatus")){
        $("missingStatus").textContent="Showing the browser-saved checklist. Use Refresh checklist to save the case and update the database version.";
        $("missingStatus").className="inline-note";
      }
      if(extractedStatus){extractedStatus.textContent="Showing information saved in this browser; this case has no linked database incident yet.";extractedStatus.className="inline-note";}
      return;
    }
    const headers=caseApiHeaders(caseId);
    const requests=[
      apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId),{headers}),
      apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/evidence",{headers}),
      apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/timeline",{headers}),
      apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/missing-information",{headers}),
      apiJson(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/complaint",{headers})
    ];
    const [incident,records,timeline,missing,complaint]=await Promise.all(requests);
    if(!isCurrent())return;
    const current=caseById(caseId);
    if(!current)return;
    const state=stateFromIncident(incident.incident||incident,current.state||defaultState());
    state.events=(timeline.events||[]).map(event=>({
      date:event.date||"",
      date_text:event.date_text||"",
      time:event.time?String(event.time).slice(0,5):"",
      description:event.description||"",
      source:event.source||"User entry",
      evidence_id:event.evidence_id||null,
      evidence_filename:event.evidence_filename||"",
      evidenceName:event.evidence_filename||"",
      origin:event.origin||"user",
      review_status:event.review_status||"candidate"
    }));
    state.serverMissingInformation=missing;
    state.draft=complaint.text||"";
    state.complaintNotice=complaint.notice||"";
    state.guidance=(incident.incident||incident).workflow_data?.guidance||null;
    setCaseState(caseId,state);
    mergeEvidenceRecords(records,caseId);
    if(hasTimeline&&$("timelineStatus"))$("timelineStatus").textContent=timeline.notice||"Saved timeline loaded from the selected incident.";
    if(hasMissing&&$("missingStatus"))$("missingStatus").textContent="Saved checklist loaded from current incident details.";
    if(hasComplaint&&$("complaintStatus"))$("complaintStatus").textContent=complaint.notice||"Complaint draft loaded from the selected incident.";
    if(hasGuidance&&state.guidance){
      $("guidanceQuery").value=state.guidance.query||"";
      renderGuidanceResults(state.guidance);
      if($("guidanceStatus"))$("guidanceStatus").textContent="Saved guidance results restored from the database. Verify current instructions with the official publisher.";
    }
    if(extractedStatus){extractedStatus.textContent="Saved evidence and extracted candidates loaded from the database.";extractedStatus.className="inline-note success";}
    refreshShared();
  }catch(error){
    if(!isCurrent())return;
    const message=error instanceof Error?error.message:"Saved workflow information could not be loaded.";
    ["timelineStatus","missingStatus","complaintStatus"].forEach(id=>{
      if($(id)&&!$(id).textContent)$(id).textContent=message;
    });
    if(extractedStatus){extractedStatus.textContent=message;extractedStatus.className="inline-note error";}
  }
}
async function deleteEvidenceItem(index){
  const item=getState().evidence[index];
  if(!item||!confirm("Delete "+item.name+" from this incident?"))return;
  try{
    if(item.evidenceId){
      const response=await fetch(EVIDENCE_API_URL+"/"+encodeURIComponent(item.evidenceId),{method:"DELETE",headers:caseApiHeaders()});
      if(!response.ok){
        const error=await response.json();
        throw new Error(error.detail||"Evidence could not be deleted (HTTP "+response.status+").");
      }
    }
    const state=getState(),removed=state.evidence.splice(index,1)[0];
    if(removed)state.events=state.events.map(event=>event.evidenceName===removed.name?{...event,evidenceName:"",evidence_filename:"",evidence_id:null}:event);
    setState(state);refreshShared();
    let timelineSyncError="";
    if(removed?.evidenceId){
      try{await saveTimelineToApi(state.events);}
      catch(error){timelineSyncError=error instanceof Error?error.message:"Timeline evidence link could not be updated.";}
    }
    if(removed?.evidenceId===$("extractionPreview")?.dataset.evidenceId)closeEvidenceTextPreview();
    logActivity("evidence","Evidence item deleted","An evidence item was deleted from the selected incident.","updated");
    setEvidenceStatus(timelineSyncError?"Evidence deleted, but the timeline link could not be updated: "+timelineSyncError:"Evidence deleted successfully.",timelineSyncError?"error":"success");
    notify(timelineSyncError?"Evidence deleted; timeline needs attention.":"Evidence item deleted.");
  }catch(error){
    setEvidenceStatus(error instanceof Error?error.message:"Evidence could not be deleted.","error");
    notify("Evidence could not be deleted.");
  }
}
function closeEvidenceTextPreview(){
  const preview=$("extractionPreview");
  if(preview){preview.hidden=true;delete preview.dataset.evidenceId;}
  $("extractedTextContent")?.replaceChildren();
  if($("extractedTextContent"))$("extractedTextContent").hidden=true;
  const facts=$("structuredFactsPreview");if(facts)facts.hidden=true;
}
async function requestEvidenceText(evidenceId,extract,mode="text"){
  if(!evidenceId||extractingEvidenceId)return;
  const item=getState().evidence.find(entry=>entry.evidenceId===evidenceId);
  if(!item)return;
  extractingEvidenceId=evidenceId;
  const preview=$("extractionPreview"),filename=$("extractionPreviewFilename"),status=$("extractionPreviewStatus"),content=$("extractedTextContent");
  if(preview){preview.hidden=false;preview.dataset.evidenceId=evidenceId;}
  if(filename)filename.textContent=item.name;
  if(status){status.textContent=extract?(mode==="ocr"?"Running OCR…":"Extracting text…"):"Loading saved extraction…";status.className="inline-note pending";}
  if(content){content.textContent="";content.hidden=true;}
  renderEvidenceTable();
  try{
    const endpoint=EVIDENCE_API_URL+"/"+encodeURIComponent(evidenceId)+(extract?(mode==="ocr"?"/ocr":"/extract-text"):"/text");
    const response=await fetch(endpoint,{method:extract?"POST":"GET",headers:caseApiHeaders()});
    let result;
    try{result=await response.json()}catch{throw new Error("The API returned an unreadable extraction response.");}
    if(!response.ok)throw new Error(result.detail||"Text extraction could not be completed (HTTP "+response.status+").");
    const state=getState(),updated=state.evidence.find(entry=>entry.evidenceId===evidenceId);
    if(updated){
      updated.extractionStatus=result.status||"not_extracted";
      updated.extractionMessage=result.message||"";
      updated.extractedAt=result.extracted_at||null;
    }
    let informationError="";
    if(updated&&["extracted","ocr_extracted"].includes(result.status)&&result.extracted_text&&
       (extract||!updated.structuredData)){
      try{
        const information=await apiJson(EVIDENCE_API_URL+"/"+encodeURIComponent(evidenceId)+"/extract-information",{method:"POST",headers:caseApiHeaders()});
        updated.structuredData=information.structured_data||null;
      }catch(error){
        informationError=error instanceof Error?error.message:"Candidate facts could not be saved.";
      }
    }
    if(updated)setState(state);
    if(status){
      status.textContent=(result.message||extractionStatusLabel(result.status))+(informationError?" Candidate extraction could not be saved: "+informationError:"");
      status.className="inline-note "+(informationError?"error":["extracted","ocr_extracted"].includes(result.status)?"success":result.status==="not_extracted"?"":"error");
    }
    if(content&&["extracted","ocr_extracted"].includes(result.status)&&result.extracted_text){
      content.textContent=result.extracted_text;
      content.hidden=false;
    }
    renderStructuredFacts(updated||item);
    refreshShared();
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"Text extraction could not be completed.";status.className="inline-note error";}
  }finally{
    extractingEvidenceId=null;
    renderEvidenceTable();
  }
}
async function addEvidenceFiles(files,source){
  if(uploadingEvidence)return;
  const allowed=files.filter(file=>/\.(pdf|png|jpe?g|txt)$/i.test(file.name)&&file.size<=10*1024*1024);
  const invalid=files.length-allowed.length;
  if(!allowed.length){
    const message=invalid?"Select PDF, PNG, JPG, JPEG, or TXT files no larger than 10 MB.":"Choose a file to upload.";
    setEvidenceStatus(message,"error");notify(message);return;
  }
  uploadingEvidence=true;
  const fileInput=$("evidenceFiles");
  if(fileInput)fileInput.disabled=true;
  const category=$("evidenceCategory")?.value||"Other";
  setEvidenceStatus("Uploading "+allowed.length+" file(s) to the selected incident…","pending");
  let uploaded=0;
  const failures=[];
  try{
    const incidentId=await ensureActiveIncidentId();
    for(const file of allowed){
      try{
        const formData=new FormData();
        formData.append("file",file,file.name);
        formData.append("category",category);
        const response=await fetch(INCIDENT_API_URL+"/"+encodeURIComponent(incidentId)+"/evidence",{
          method:"POST",
          headers:caseApiHeaders(),
          body:formData
        });
        if(!response.ok){
          const error=await response.json();
          throw new Error(error.detail||"Upload failed (HTTP "+response.status+").");
        }
        const record=await response.json(),state=getState();
        state.evidence=[evidenceFromApi(record),...state.evidence.filter(item=>item.evidenceId!==record.id)];
        setState(state);
        uploaded++;
        refreshShared();
      }catch(error){
        failures.push(file.name+": "+(error instanceof Error?error.message:"Upload failed."));
      }
    }
    const sourceText=source==="drop"?" by drag and drop":" from the file picker";
    if(uploaded){
      logActivity("evidence","Evidence files uploaded",uploaded+" evidence file(s) uploaded"+sourceText+".","completed");
      await loadEvidenceInventory();
    }
    const messages=[];
    if(uploaded)messages.push(uploaded+" file(s) uploaded successfully.");
    if(invalid)messages.push(invalid+" unsupported or over-10-MB file(s) skipped.");
    if(failures.length)messages.push(failures.join(" "));
    const kind=failures.length||invalid?"error":"success";
    setEvidenceStatus(messages.join(" ")||"No files were uploaded.",kind);
    notify(uploaded?uploaded+" evidence file(s) uploaded.":messages.join(" "));
  }catch(error){
    const message=error instanceof Error?error.message:"Files could not be uploaded.";
    setEvidenceStatus(message,"error");
    notify("Evidence upload failed.");
  }finally{
    uploadingEvidence=false;
    if(fileInput){fileInput.disabled=false;fileInput.value="";}
  }
}
const fileInput=$("evidenceFiles");
if(fileInput)fileInput.addEventListener("change",event=>{addEvidenceFiles(Array.from(event.target.files||[]),"picker");});
const closeExtractionPreview=$("closeExtractionPreview");
if(closeExtractionPreview)closeExtractionPreview.addEventListener("click",closeEvidenceTextPreview);
const manualEvidenceForm=$("manualEvidenceForm"),manualEvidenceButton=$("addManualEvidence");
if(manualEvidenceButton&&manualEvidenceForm)manualEvidenceButton.addEventListener("click",()=>{manualEvidenceForm.hidden=false;manualEvidenceButton.setAttribute("aria-expanded","true");$("manualEvidenceName")?.focus();});
const cancelManualEvidence=$("cancelManualEvidence");
if(cancelManualEvidence)cancelManualEvidence.addEventListener("click",()=>{manualEvidenceForm?.reset();if(manualEvidenceForm)manualEvidenceForm.hidden=true;manualEvidenceButton?.setAttribute("aria-expanded","false");manualEvidenceButton?.focus();});
if(manualEvidenceForm)manualEvidenceForm.addEventListener("submit",event=>{
  event.preventDefault();const name=$("manualEvidenceName").value.trim();if(!name)return;
  const category=$("manualEvidenceCategory").value||"Other",state=getState();
  state.evidence.push({name,size:0,type:"Manual entry",category,added:new Date().toLocaleDateString("en-IN")});
  setState(state);manualEvidenceForm.reset();manualEvidenceForm.hidden=true;manualEvidenceButton?.setAttribute("aria-expanded","false");
  refreshShared();logActivity("evidence","Evidence item added",name+" added to the evidence inventory.","completed");notify("Evidence item added.");
});
const refresh=$("refreshExtracted");if(refresh)refresh.addEventListener("click",()=>{loadCurrentWorkflow();renderExtracted();notify("Information view refreshed.")});
const runAiExtractionButton=$("runAiExtraction");
if(runAiExtractionButton)runAiExtractionButton.addEventListener("click",runAiEvidenceExtraction);
const processBtn=$("runDemoProcessing");if(processBtn)processBtn.addEventListener("click",()=>{renderProcessing();const count=getState().evidence.length;logActivity("workflow","Evidence inventory reviewed",count?count+" file(s) listed. PDF/TXT extraction, OCR, local candidate extraction, and consent-based AI extraction are available.":"No evidence files are listed.","updated");notify("Evidence inventory status updated.")});
const addEvent=$("addTimelineEvent");
if(addEvent)addEvent.addEventListener("click",()=>{
  const form=$("timelineForm");form?.reset();if(form)delete form.dataset.editIndex;
  $("timelineFormTitle").textContent="Add timeline event";$("saveTimelineEvent").textContent="Add event";
  $("eventFormPanel").hidden=false;$("eventDate").focus();
});
const cancelEvent=$("cancelEvent");if(cancelEvent)cancelEvent.addEventListener("click",finishTimelineEdit);
const timelineForm=$("timelineForm");
if(timelineForm)timelineForm.addEventListener("submit",async event=>{
  event.preventDefault();
  const state=getState(),evidenceIndex=$("eventEvidenceSource")?.value,linkedEvidence=evidenceIndex?state.evidence[Number(evidenceIndex)]:null;
  const description=$("eventDescription").value.trim(),eventDate=$("eventDate").value,eventTime=$("eventTime").value;
  const updated={date:eventDate,time:eventTime,title:"User-added event",description,source:"Manual timeline entry",evidenceName:linkedEvidence?.name||"",evidence_filename:linkedEvidence?.name||"",evidence_id:linkedEvidence?.evidenceId||null,origin:"user",review_status:"reviewed"};
  const editIndex=timelineForm.dataset.editIndex;
  if(editIndex!==undefined){state.events[Number(editIndex)]=updated;logActivity("timeline","Timeline event updated",description,"updated");notify("Timeline event updated.");}
  else{state.events.push(updated);logActivity("timeline","Timeline event added",description,"completed");notify("Timeline event added.");}
  setState(state);finishTimelineEdit();refreshShared();
  const status=$("timelineStatus");
  if(status){status.textContent="Saving timeline…";status.className="inline-note pending";}
  try{
    await saveTimelineToApi(state.events);
    if(status){status.textContent="Timeline event saved to the database.";status.className="inline-note success";}
  }catch(error){
    if(status){status.textContent=error instanceof Error?error.message:"Timeline event could not be saved.";status.className="inline-note error";}
  }
});
const generateTimelineButton=$("generateTimeline");
if(generateTimelineButton)generateTimelineButton.addEventListener("click",generateTimelineFromApi);
const refreshMissingButton=$("refreshMissing");
if(refreshMissingButton)refreshMissingButton.addEventListener("click",refreshMissingInformation);
document.querySelectorAll("[data-guidance]").forEach(btn=>btn.addEventListener("click",()=>{const el=$("guide-"+btn.dataset.guidance);el.hidden=!el.hidden;btn.setAttribute("aria-expanded",String(!el.hidden));btn.textContent=el.hidden?"View checklist →":"Hide checklist ↑"}));
const regen=$("regenerateDraft");
if(regen)regen.addEventListener("click",generateComplaintFromApi);
const complaintEditor=$("complaintEditor");
if(complaintEditor){
  complaintEditor.addEventListener("input",()=>{
    const caseId=activeCase().id,text=complaintEditor.value;
    const state={...defaultState(),...(caseById(caseId)?.state||{})};state.draft=text;setCaseState(caseId,state);renderReview();
    clearTimeout(complaintSaveTimer);
    complaintSaveTimer=setTimeout(async()=>{
      const status=$("complaintStatus");
      try{
        await saveComplaintToApi(text,false,caseId);
        if(status&&activeCase().id===caseId){status.textContent="Draft edits saved to the database.";status.className="inline-note success";}
      }catch(error){
        if(status&&activeCase().id===caseId){status.textContent=error instanceof Error?error.message:"Draft edits could not be saved.";status.className="inline-note error";}
      }
    },700);
  });
}
const saveDraftButton=$("saveDraft");
if(saveDraftButton)saveDraftButton.addEventListener("click",async()=>{
  clearTimeout(complaintSaveTimer);
  const caseId=activeCase().id,state={...defaultState(),...(caseById(caseId)?.state||{})};
  state.draft=$("complaintEditor")?.value??state.draft;setCaseState(caseId,state);
  const status=$("complaintStatus");
  if(status){status.textContent="Saving draft…";status.className="inline-note pending";}
  try{
    await saveComplaintToApi(state.draft,false,caseId);
    if(status&&activeCase().id===caseId){status.textContent="Draft saved in the database.";status.className="inline-note success";}
    renderDashboard();renderReview();logActivity("draft","Complaint draft saved","Complaint draft edits were saved to the database.","completed");notify("Draft saved.");
  }catch(error){
    if(status&&activeCase().id===caseId){status.textContent=error instanceof Error?error.message:"Draft could not be saved.";status.className="inline-note error";}
    if(activeCase().id===caseId)notify("Draft could not be saved.");
  }
});
[$("exportComplaintTxt"),$("exportFinal")].filter(Boolean).forEach(button=>button.addEventListener("click",()=>downloadComplaint("txt")));
[$("exportComplaintPdf"),$("exportFinalPdf")].filter(Boolean).forEach(button=>button.addEventListener("click",()=>downloadComplaint("pdf")));
const guidanceSearchForm=$("guidanceSearchForm");
if(guidanceSearchForm)guidanceSearchForm.addEventListener("submit",event=>{
  event.preventDefault();searchGuidance($("guidanceQuery").value.trim());
});
const login=$("loginForm");if(login)login.addEventListener("submit",async e=>{
  e.preventDefault();
  const feedback=$("loginFeedback");
  try{
    await selectDemoScope($("loginEmail").value);
    localStorage.setItem(DEMO_AUTH_KEY,"active");
    window.location.href="dashboard.html";
  }catch(error){
    if(feedback){feedback.textContent=error instanceof Error?error.message:"A demo workspace could not be selected.";feedback.hidden=false;}
  }
});
document.querySelectorAll("[data-password-toggle]").forEach(button=>button.addEventListener("click",()=>{
  const input=$(button.dataset.passwordToggle);
  if(!input)return;
  const visible=input.type==="password";
  input.type=visible?"text":"password";
  button.setAttribute("aria-label",visible?"Hide password":"Show password");
  button.setAttribute("aria-pressed",String(visible));
}));
[$("loginForm"),$("signupForm")].filter(Boolean).forEach(form=>{
  const feedback=form.querySelector(".form-error");
  form.addEventListener("invalid",event=>{
    if(feedback){feedback.textContent=event.target.validationMessage;feedback.hidden=false;}
  },true);
  form.addEventListener("input",()=>{
    if(feedback&&(!form.id.includes("signup")||!$("signupPasswordConfirm").validationMessage)){
      feedback.textContent="";feedback.hidden=true;
    }
  });
});
const signup=$("signupForm");
if(signup){
  const password=$("signupPassword"),confirmation=$("signupPasswordConfirm");
  const feedback=$("signupFeedback");
  const validatePasswords=()=>{
    const message=confirmation.value&&confirmation.value!==password.value?"Passwords do not match.":"";
    confirmation.setCustomValidity(message);
    if(feedback){feedback.textContent=message;feedback.hidden=!message;}
  };
  password.addEventListener("input",validatePasswords);
  confirmation.addEventListener("input",validatePasswords);
  signup.addEventListener("submit",async e=>{
    validatePasswords();
    if(!signup.reportValidity()){e.preventDefault();return}
    e.preventDefault();
    try{
      await selectDemoScope($("signupEmail").value);
      localStorage.setItem(DEMO_AUTH_KEY,"active");
      window.location.href="dashboard.html";
    }catch(error){
      if(feedback){feedback.textContent=error instanceof Error?error.message:"A demo workspace could not be selected.";feedback.hidden=false;}
    }
  });
}
function startNewCase(){const created=createCase("");logActivity("incident","New incident case created",created.id+" created.","completed");notify("New case created: "+created.id);window.location.href="incident.html"}
const createCaseButton=$("createCaseButton"),dashboardCreateCase=$("dashboardCreateCase");
if(createCaseButton)createCaseButton.addEventListener("click",startNewCase);
if(dashboardCreateCase)dashboardCreateCase.addEventListener("click",startNewCase);

refreshShared();
if($("dashboardRecentCases"))loadSavedIncidents();
if($("evidenceTable"))loadEvidenceInventory();
if($("timelineEvents")||$("missingChecks")||$("complaintEditor")||$("extractedTable")||$("guidanceResults"))loadCurrentWorkflow();
if($("knowledgeBaseStatus"))loadKnowledgeBaseStatus();


// Drag-and-drop upload affordance for the evidence inventory.
const uploadZone = document.querySelector(".upload-zone");
if (uploadZone && fileInput) {
  uploadZone.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.click();
    }
  });
  ["dragenter","dragover"].forEach(type => uploadZone.addEventListener(type, e => {
    e.preventDefault(); uploadZone.classList.add("dragover");
  }));
  ["dragleave","drop"].forEach(type => uploadZone.addEventListener(type, e => {
    e.preventDefault(); uploadZone.classList.remove("dragover");
  }));
  uploadZone.addEventListener("drop", e => {
    const files = Array.from(e.dataTransfer?.files || []);
    addEvidenceFiles(files,"drop");
  });
}
// Keep links feeling like a real app when changing pages, while respecting reduced-motion preferences.
document.querySelectorAll('a[href$=".html"]').forEach(link => {
  link.addEventListener("click", e => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = link.getAttribute("href");
    if (!href || href.startsWith("#") || href === window.location.pathname.split("/").pop()) return;
    document.body.classList.add("page-leaving");
  });
});


// Persistent light/dark appearance across every page.
(function initTheme(){
  const themeKey = "cyberassist-theme";
  const saved = localStorage.getItem(themeKey);
  document.body.dataset.theme = saved || "dark";
  function paintButtons(){
    document.querySelectorAll("[data-theme-toggle]").forEach(btn=>{
      const dark = document.body.dataset.theme === "dark";
      const label = btn.querySelector(".theme-label");
      const glyph = btn.querySelector(".theme-glyph");
      if(label) label.textContent = dark ? "Light mode" : "Dark mode";
      if(glyph) glyph.textContent = dark ? "☼" : "◐";
      btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      btn.setAttribute("aria-pressed", String(dark));
    });
  }
  paintButtons();
  document.querySelectorAll("[data-theme-toggle]").forEach(btn=>btn.addEventListener("click",()=>{
    const next = document.body.dataset.theme === "dark" ? "light" : "dark";
    document.body.dataset.theme = next;
    localStorage.setItem(themeKey,next);
    paintButtons();
  }));
})();


// Workspace activity history: recorded locally in this browser.
const HISTORY_KEY = "cyberassist-activity-history-v1";
function readHistory(){try{return JSON.parse(localStorage.getItem(scopedKey(HISTORY_KEY))||"[]")}catch(e){return []}}
function logActivity(type,title,details,status="updated"){
  const rows=readHistory();
  rows.unshift({id:Date.now()+"-"+Math.random().toString(16).slice(2,7),caseId:localStorage.getItem(scopedKey(ACTIVE_CASE_KEY))||"CA-LOCAL-001",type,title,details,status,at:new Date().toISOString()});
  localStorage.setItem(scopedKey(HISTORY_KEY),JSON.stringify(rows.slice(0,250)));
  renderHistory();
}
function currentWorkflowStatus(){
  const s=getState();
  const tasks=[{name:"Incident details",done:!!(s.summary||"").trim(),type:"incident"}];
  if(s.evidence.length)tasks.push({name:"Evidence inventory",done:true,type:"evidence"});
  tasks.push(
    {name:"Timeline review",done:timelineEvents(s).length>0,type:"timeline"},
    {name:"Complaint draft",done:!!(s.draft||s.summary),type:"draft"}
  );
  return tasks;
}
function renderCaseRegister(){
  const host=$("caseRegister");if(!host)return;
  const q=($("caseSearch")?.value||"").trim().toLowerCase(),filter=$("caseStatusFilter")?.value||"all",activeId=localStorage.getItem(scopedKey(ACTIVE_CASE_KEY));
  const rows=getCaseRows().map(({c,s,serverBacked,incident})=>{
  const tasks=[{name:"Incident details",done:!!(s.summary||"").trim()}];
  if(s.evidence.length)tasks.push({name:"Evidence inventory",done:true});
  tasks.push(
    {name:"Timeline review",done:timelineEvents(s).length>0},
    {name:"Complaint draft",done:!!(s.draft||s.summary)}
  );
  const hasInfo=!!(s.incidentType||s.incidentDate||s.incidentTime||s.platform||s.reportedIdentifier||s.transactionReference||s.lossAmount||s.location||s.summary||s.requestedAction||s.evidence.length||s.events.length||s.draft);
  const done=tasks.filter(task=>task.done).length;
  const status=!hasInfo?"not-started":done===tasks.length?"review":"in-progress";
  return {c,s,serverBacked,incident,tasks,done,status,statusText:status==="not-started"?"Not started":status==="review"?"Ready for review":"In progress",category:s.incidentType||"Incident type not specified",next:tasks.find(task=>!task.done)};
});
const filtered=rows.filter(r=>(r.c.id+" "+r.category+" "+r.statusText+" "+(r.s.platform||"")).toLowerCase().includes(q)&&(filter==="all"||filter===r.status));
  const status=$("caseRegisterStatus");
  if(status){
    status.classList.toggle("error",!!savedIncidentsError);
    status.classList.toggle("pending",savedIncidentsLoading&&!savedIncidentsError);
    status.textContent=savedIncidentsError
      ?"Database cases could not be loaded ("+savedIncidentsError+"). Browser-saved cases are still shown."
      :savedIncidentsLoading?"Loading saved cases from the database…"
      :"Database cases are combined with browser-only cases.";
  }
  if(!filtered.length){host.innerHTML='<div class="empty-table">No incident cases match these filters. Create a new case to get started.</div>';return;}
  host.innerHTML='<div class="case-register-table-wrap"><table class="data-table case-register-table"><thead><tr><th>CASE ID</th><th>INCIDENT</th><th>INCIDENT DATE</th><th>PROGRESS</th><th>STATUS</th><th>NEXT STEP</th><th></th></tr></thead><tbody>'+filtered.map(r=>{const progress=Math.round(r.done/r.tasks.length*100);const displayId=r.c.id.startsWith("CA-")?r.c.id:"CA-"+r.c.id.slice(0,8).toUpperCase();return `<tr class="${r.c.id===activeId?"active-case-row":""}"><td><b>${safe(displayId)}</b><small class="case-subtext">${r.c.id===activeId?"Active case":r.serverBacked?"Database case":"Browser-only case"}</small></td><td><b>${safe(r.category)}</b><small class="case-subtext">${safe(r.s.platform||"Platform not provided")}</small></td><td>${safe(fmtDate(r.s.incidentDate))}</td><td><div class="case-progress"><span><i style="width:${progress}%"></i></span><small>${r.done} of ${r.tasks.length} steps</small></div></td><td><span class="case-status ${r.status}">${r.statusText}</span></td><td>${safe(r.next?r.next.name:"Review complaint draft")}</td><td><button class="case-open-link" type="button" data-open-case="${safe(r.c.id)}" data-backend-id="${safe(r.incident?.id||r.c.backendId||"")}">${r.c.id===activeId?"Open →":"Switch →"}</button></td></tr>`}).join("")+'</tbody></table></div>';
  host.querySelectorAll("[data-open-case]").forEach(btn=>btn.addEventListener("click",()=>{
    const incident=savedIncidents.find(item=>item.id===btn.dataset.backendId);
    if(incident)activateServerIncident(incident);
    else{
      const selected=getCases().find(item=>item.id===btn.dataset.openCase);
      if(!selected)return;
      activateLocalCase(selected.id);
    }
    window.location.href="dashboard.html";
  }));
}
function renderHistory(){
  renderCaseRegister();
  const table=$("historyTable"); if(!table)return;
  const logs=readHistory(), tasks=currentWorkflowStatus();
  const statusFilter=$("historyStatus")?.value||"all";
  const typeFilter=$("historyType")?.value||"all";
  const query=($("historySearch")?.value||"").trim().toLowerCase();
  $("historyTotal").textContent=logs.length;
  $("historyCompleted").textContent=logs.filter(x=>x.status==="completed").length;
  $("historyPending").textContent=tasks.filter(x=>!x.done).length;
  $("historyLatest").textContent=logs.length?new Date(logs[0].at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}):"—";
  $("historyWorkflowSummary").textContent=tasks.filter(x=>x.done).length+" of "+tasks.length+" workflow steps have information recorded. Pending steps: "+tasks.filter(x=>!x.done).map(x=>x.name).join(", ")+"." ;
  const pendingRows=tasks.filter(x=>!x.done).map(x=>({id:"pending-"+x.type,type:x.type,title:x.name+" pending",details:"This step has not been completed in the current workspace.",status:"pending",at:null}));
  const activeId=localStorage.getItem(scopedKey(ACTIVE_CASE_KEY))||"CA-LOCAL-001";
  const scopedLogs=logs.map(x=>({...x,caseId:x.caseId||"CA-LOCAL-001"}));
  const allRows=[...scopedLogs,...pendingRows.map(x=>({...x,caseId:activeId}))];
  const filtered=allRows.filter(x=>(statusFilter==="all"||x.status===statusFilter)&&(typeFilter==="all"||x.type===typeFilter)&&(!query||(x.title+" "+x.details+" "+x.type+" "+x.caseId).toLowerCase().includes(query)));
  if(!filtered.length){table.innerHTML='<div class="empty-table">No matching activity yet. Save incident details, add evidence, add timeline events, or edit a draft.</div>';return}
  table.innerHTML='<table class="data-table history-table"><thead><tr><th>CASE</th><th>ACTIVITY</th><th>DETAILS</th><th>TYPE</th><th>STATUS</th><th>DATE & TIME</th></tr></thead><tbody>'+filtered.map(x=>{
    const date=x.at?new Date(x.at):null;const statusClass=x.status==="completed"?"completed":x.status==="pending"?"pending":"updated";
    return `<tr><td><b>${safe(x.caseId||"CA-LOCAL-001")}</b></td><td><b>${safe(x.title)}</b></td><td>${safe(x.details)}</td><td><span class="history-type">${safe(x.type)}</span></td><td><span class="history-status ${statusClass}">${safe(x.status)}</span></td><td>${date?safe(date.toLocaleDateString("en-IN",{day:"2-digit",month:"short",year:"numeric"})):'—'}${date?'<small class="history-time">'+safe(date.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}))+'</small>':''}</td></tr>`;
  }).join("")+'</tbody></table>';
}
if($("historyTable")){
  ["historySearch","historyStatus","historyType"].forEach(id=>$(id)?.addEventListener(id==="historySearch"?"input":"change",renderHistory));
  $("caseSearch")?.addEventListener("input",renderCaseRegister);
  $("caseStatusFilter")?.addEventListener("change",renderCaseRegister);
  $("historyRefresh")?.addEventListener("click",()=>{renderHistory();loadSavedIncidents(true);notify("Activity history refreshed.")});
  $("historyClear")?.addEventListener("click",()=>{if(confirm("Clear all recorded activity history?")){localStorage.removeItem(scopedKey(HISTORY_KEY));renderHistory();notify("Activity history cleared.")}});
  renderHistory();
  loadSavedIncidents();
}

(function initWorkspaceNavigation(){
  const shell=document.querySelector(".app-shell"),sidebar=document.querySelector(".sidebar"),topbar=document.querySelector(".topbar");
  if(!shell||!sidebar||!topbar)return;
  const icons={
    "dashboard.html":'<svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
    "incident.html":'<svg viewBox="0 0 24 24"><path d="M8 4.5h8a2 2 0 0 1 2 2v13H6v-13a2 2 0 0 1 2-2Z"/><path d="M9 4.5v-1h6v1M9 10h6M9 14h6"/></svg>',
    "evidence.html":'<svg viewBox="0 0 24 24"><path d="M3.5 7.5h7l2 2h8v9h-17z"/><path d="M3.5 7.5v-2h7l2 2"/></svg>',
    "timeline.html":'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/></svg>',
    "missing-info.html":'<svg viewBox="0 0 24 24"><path d="M6 4.5h12v16H6z"/><path d="m8.5 9 1.5 1.5L12.5 8M14.5 9h1M8.5 15l1.5 1.5 2.5-2.5M14.5 15h1"/></svg>',
    "complaint.html":'<svg viewBox="0 0 24 24"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4M9 12h6M9 16h6"/></svg>',
    "history.html":'<svg viewBox="0 0 24 24"><path d="M4 11a8 8 0 1 1 2.4 5.7"/><path d="M4 5.5V11h5.5M12 7v5l3 2"/></svg>',
    "processing.html":'<svg viewBox="0 0 24 24"><path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/><circle cx="12" cy="12" r="4"/></svg>',
    "extracted-info.html":'<svg viewBox="0 0 24 24"><path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>',
    "review.html":'<svg viewBox="0 0 24 24"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4m-9 5 2 2 4-4"/></svg>',
    "guidance.html":'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M9.5 9a2.5 2.5 0 1 1 4.3 1.8c-1.1 1.1-1.8 1.4-1.8 3.2M12 17.5h.01"/></svg>'
  };
  sidebar.id="workspaceNavigation";
  const nav=sidebar.querySelector(".nav");
  if(nav){
    nav.setAttribute("aria-label","Workspace navigation");
    const section=document.createElement("span");
    section.className="nav-section-label";
    section.textContent="REVIEW & GUIDANCE";
    nav.appendChild(section);
    [["processing.html","Evidence processing"],["extracted-info.html","Information review"],["review.html","Final review"],["guidance.html","Reporting guidance"]].forEach(([href,label])=>{
      if(nav.querySelector(`.nav-link[href="${href}"]`))return;
      const link=document.createElement("a");
      link.className="nav-link";
      link.href=href;
      link.innerHTML=`<span>${label}</span>`;
      nav.appendChild(link);
    });
  }
  sidebar.querySelectorAll(".nav-link").forEach(link=>{
    const href=link.getAttribute("href")||"";
    const oldIcon=link.querySelector(".nav-code");
    if(oldIcon)oldIcon.remove();
    const icon=document.createElement("span");
    icon.className="nav-icon";
    icon.setAttribute("aria-hidden","true");
    icon.innerHTML=icons[href]||icons["dashboard.html"];
    link.insertBefore(icon,link.firstChild);
    const isCurrent=href===window.location.pathname.split("/").pop();
    link.classList.toggle("active",isCurrent);
    if(isCurrent)link.setAttribute("aria-current","page");
    else link.removeAttribute("aria-current");
  });
  const toggle=document.createElement("button");
  toggle.className="sidebar-toggle";
  toggle.type="button";
  toggle.setAttribute("aria-controls",sidebar.id);
  toggle.setAttribute("aria-expanded","false");
  toggle.setAttribute("aria-label","Open workspace navigation");
  toggle.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>Menu</span>';
  topbar.insertBefore(toggle,topbar.firstChild);
  const topbarActions=topbar.querySelector(".topbar-actions");
  if(topbarActions){
    const logout=document.createElement("button");
    logout.className="btn logout-button";
    logout.type="button";
    logout.title="Demo logout only. Locally saved case data will remain in this browser.";
    logout.setAttribute("aria-label","Log out of the demo workspace. Saved case data will remain in this browser.");
    logout.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5H5.5v14H10M14 8l4 4-4 4M8.5 12H18"/></svg><span>Logout</span>';
    logout.addEventListener("click",()=>{
      if(!window.confirm("Log out of the Cyber Assist demo? This only ends the demo login state. Your incident cases, evidence metadata, timeline events, and complaint drafts will remain saved in this browser."))return;
      localStorage.removeItem(DEMO_AUTH_KEY);
      sessionStorage.removeItem(DEMO_SCOPE_KEY);
      workflowLoadToken++;
      evidenceLoadToken++;
      window.location.href="login.html";
    });
    topbarActions.appendChild(logout);
  }
  const close=()=>{
    document.body.classList.remove("sidebar-open");
    toggle.setAttribute("aria-expanded","false");
    toggle.setAttribute("aria-label","Open workspace navigation");
  };
  toggle.addEventListener("click",()=>{
    const open=!document.body.classList.contains("sidebar-open");
    document.body.classList.toggle("sidebar-open",open);
    toggle.setAttribute("aria-expanded",String(open));
    toggle.setAttribute("aria-label",open?"Close workspace navigation":"Open workspace navigation");
  });
  sidebar.querySelectorAll(".nav-link").forEach(link=>link.addEventListener("click",close));
  document.addEventListener("click",event=>{
    if(document.body.classList.contains("sidebar-open")&&!sidebar.contains(event.target)&&!toggle.contains(event.target))close();
  });
  document.addEventListener("keydown",event=>{if(event.key==="Escape")close()});
  window.addEventListener("resize",()=>{if(window.innerWidth>760)close()});
})();
