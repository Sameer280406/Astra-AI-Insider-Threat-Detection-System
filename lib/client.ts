import type {Item,Principal,Store} from './contracts';
import seed from './demo-seed.json';
export const DEMO=process.env.NEXT_PUBLIC_DEMO_MODE!=='false';
const BASE=process.env.NEXT_PUBLIC_API_BASE_URL||'';
let state:Store=structuredClone(seed as Store);
let loaded=false;
let principal:Principal={id:'demo-admin',name:'Alex Morgan',email:'org_admin@astra.demo',role:'org_admin',organization:{id:'demo',name:'Meridian Technologies',slug:'meridian',tier:'enterprise'},features:{graph:true,integrations:true}};
export function restore(){if(loaded||typeof window==='undefined')return;loaded=true;try{const s=localStorage.getItem('astra-demo-v2');if(s)state=JSON.parse(s)}catch{}}
function save(){localStorage.setItem('astra-demo-v2',JSON.stringify(state));window.dispatchEvent(new Event('astra-update'))}
function id(){return globalThis.crypto?.randomUUID?.()||'demo-'+Date.now()+'-'+Math.random().toString(16).slice(2)}
export function resetDemo(){localStorage.removeItem('astra-demo-v2');window.location.reload()}
function audit(action:string,target:string){state.audit.unshift({id:id(),action,target,actor:principal.name,createdAt:new Date().toISOString(),hash:'Browser demo: no cryptographic audit guarantee'})}
const inFlight = new Map<string, Promise<any>>();
const readCache = new Map<string, {data: any; expires: number}>();

export async function api<T=any>(path:string,method='GET',body?:unknown):Promise<T>{
 if(!DEMO){
  const isGet = method.toUpperCase() === 'GET';
  const now = Date.now();
  
  // 1. Check short-lived cache for GET requests (1000ms TTL)
  if(isGet){
   const cached = readCache.get(path);
   if(cached && cached.expires > now){
    return cached.data as T;
   }
   // 2. In-flight request deduplication: return existing promise if identical GET is in progress
   const active = inFlight.get(path);
   if(active){
    return active as Promise<T>;
   }
  }

  const exec = (async (): Promise<T> => {
   try{
    const headers: Record<string,string> = {'Content-Type':'application/json','X-Astra-Request':'1'};
    const token = typeof window !== 'undefined' ? localStorage.getItem('astra_token') : null;
    if(token){
     headers['Authorization'] = `Bearer ${token}`;
    }
    const r=await fetch(BASE+'/api/v1'+path,{
     method,
     credentials:'include',
     headers,
     body:body===undefined?undefined:JSON.stringify(body)
    });

    // Handle 429 Rate Limiting with Retry-After
    if(r.status === 429){
     const retryHeader = r.headers.get('Retry-After');
     const jsonErr: any = await r.json().catch(() => ({}));
     const retryAfter = Number(retryHeader || jsonErr.retryAfter || 2);

     if(isGet){
      // For background GET reads, check if we have any stale cache to serve smoothly
      const stale = readCache.get(path);
      if(stale){
       console.warn(`[Astra] Rate limited on ${path}; serving stale cached data. Retry after ${retryAfter}s`);
       return stale.data as T;
      }
      // If brief backoff, wait and retry once
      if(retryAfter <= 2){
       await new Promise(res => setTimeout(res, retryAfter * 1000));
       const retryResp = await fetch(BASE+'/api/v1'+path,{
        method,
        credentials:'include',
        headers,
        body:body===undefined?undefined:JSON.stringify(body)
       });
       if(retryResp.ok){
        const retryData = await retryResp.json();
        readCache.set(path, {data: retryData, expires: Date.now() + 1200});
        return retryData as T;
       }
      }
      console.warn(`[Astra] Rate limit active on ${path}. Gracefully falling back to baseline data.`);
      restore();
      return demo(path, method, body as any) as T;
     }

     throw new Error(`Rate limit exceeded. Please wait ${retryAfter} second${retryAfter === 1 ? '' : 's'} before retrying.`);
    }

    if(r.status === 401){
     if(typeof window !== 'undefined'){
      localStorage.removeItem('astra_token');
      window.dispatchEvent(new CustomEvent('astra-auth-expired'));
     }
     const e:any=await r.json().catch(()=>({detail:'Authentication required'}));
     throw new Error(typeof e.detail==='string'?e.detail:'Authentication required');
    }

    if(!r.ok){
     const e:any=await r.json().catch(()=>({detail:'Request failed'}));
     throw new Error(typeof e.detail==='string'?e.detail:'Check the form fields and try again');
    }

    const data = await r.json();
    if(data && (data as any).token && typeof window !== 'undefined'){
     localStorage.setItem('astra_token', (data as any).token);
    }
    if(path === '/auth/logout' && typeof window !== 'undefined'){
     localStorage.removeItem('astra_token');
    }
    if(isGet){
     // Cache successful GET responses for 1000ms
     readCache.set(path, {data, expires: Date.now() + 1000});
    }
    return data as T;
   }catch(err:any){
    if(err.message&&err.message!=='Failed to fetch'&&!err.message.includes('NetworkError')&&!err.message.includes('fetch')){throw err}
    console.warn(`[Astra] Backend at ${BASE||'local proxy'} is unreachable (${err.message}). Using browser demo store.`);
    restore();return demo(path,method,body as any) as T;
   }finally{
    if(isGet){
     inFlight.delete(path);
    }
   }
  })();

  if(isGet){
   inFlight.set(path, exec);
  }
  return exec;
 }
 restore();await new Promise(r=>setTimeout(r,80));return demo(path,method,body as any) as T;
}
function demo(path:string,method:string,b:any):any{
 const [raw,qs]=path.split('?');const parts=raw.split('/').filter(Boolean);const [kind,rid,action]=parts;const params=new URLSearchParams(qs);
 if(raw.startsWith('/response/'))return responseDemo(parts,method,b);
 if(raw==='/auth/me')return principal;
 if(raw==='/auth/providers')return {providers:[],message:'SSO is not configured. Connect an identity provider in your deployment.'};
 if(raw==='/auth/login')return principal;
 if(raw==='/auth/logout')return {ok:true};
 if(raw==='/auth/onboard'){throw new Error('Organization creation is available in the connected Docker application. The browser demo stays synthetic.')}
 if(raw==='/overview')return {entities:state.entities.length,openAlerts:state.alerts.filter(a=>a.status!=='closed').length,criticalAlerts:state.alerts.filter(a=>a.severity==='critical'&&a.status!=='closed').length,averageRisk:Math.round(state.entities.reduce((a,e)=>a+e.riskScore,0)/state.entities.length),events:state.events.length,modelMode:'mock'};
 if(raw==='/models')return ['IsolationForest','Autoencoder','LSTM','GNN','Ensemble'].map(name=>({name,mode:'mock',version:'mock-1.0',configured:true,status:'mock'}));
 if(raw==='/subscription')return {tier:principal.organization.tier,eventsUsed:state.events.length,limits:{events:1000000,users:1000,graph:true,integrations:true},plans:{starter:{events:10000,users:5},team:{events:100000,users:25},enterprise:{events:1000000,users:1000}},billingStatus:'not_connected'};
 if(raw==='/reports/export')return {organization:principal.organization,generatedAt:new Date().toISOString(),synthetic:true,alerts:state.alerts,cases:state.cases,feedback:state.feedback};
 if(raw==='/demo/simulate'){
  const e=state.entities[0],stamp=new Date().toISOString(),n=state.events.length;
  const ev={id:id(),eventId:'sim-'+n,entityId:e.id,timestamp:stamp,createdAt:stamp,action:'Bulk download',resource:'Finance / restricted',device:'EP-1042',egressMb:2500+(n%10)*20,sensitiveFiles:95,riskScore:88+(n%5),synthetic:true};
  state.events.unshift(ev);e.riskVelocity=ev.riskScore-e.riskScore;e.riskScore=ev.riskScore;e.lastSeen=stamp;
  const a={id:id(),entityId:e.id,entityName:e.name,title:'Data exfiltration',severity:'critical',status:'open',riskScore:ev.riskScore,eventId:ev.id,createdAt:stamp,explanation:'egress_mb',modelConsensus:4,synthetic:true};state.alerts.unshift(a);state.notifications.unshift({id:id(),title:a.title,body:e.name,alertId:a.id,read:false,createdAt:stamp});audit('event.scored',ev.id);demoIncident(ev,e);save();return {event:ev,alert:a};
 }
 if(kind==='cases'&&action==='similar'){const c=state.cases.find(c=>c.id===rid);return state.cases.filter(x=>x.id!==rid&&x.entityId===c?.entityId)}
 if(method==='GET'){const rows=state[kind]||[];if(rid){const row=rows.find(r=>r.id===rid);if(!row)throw new Error('Record not found');return row}return rows.filter(r=>!params.get('entityId')||r.entityId===params.get('entityId'))}
 if(method==='POST'&&kind==='cases'&&!rid){const alert=state.alerts.find(a=>a.id===b.alertId);if(!alert)throw new Error('Select an alert');const row={id:id(),...b,entityId:alert.entityId,status:'open',assignee:b.assignee||principal.id,notes:[],bookmarks:[],createdAt:new Date().toISOString()};state.cases.unshift(row);alert.status='investigating';alert.caseId=row.id;audit('case.created',row.id);save();return row}
 if(kind==='cases'&&rid){const row=state.cases.find(x=>x.id===rid);if(!row)throw new Error('Case not found');if(action==='notes')row.notes.push({text:b.text,author:principal.name,timestamp:new Date().toISOString()});else if(action==='bookmarks')row.bookmarks=[...new Set([...row.bookmarks,b.eventId])];else Object.assign(row,b);audit('case.updated',rid);save();return row}
 if(kind==='notifications'&&action==='read'){const n=state.notifications.find(n=>n.id===rid);if(n)n.read=true;save();return n}
 if(method==='PATCH'){const row=state[kind]?.find(r=>r.id===rid);if(row)Object.assign(row,b);audit(kind+'.updated',rid);save();return row}
 if(method==='POST'){if(!state[kind])state[kind]=[];const row={id:id(),...b,createdAt:new Date().toISOString()};if(kind==='integrations')Object.assign(row,{status:'adapter_required',enabled:false});if(kind==='users')delete row.password;state[kind].unshift(row);audit(kind+'.created',row.id);save();return row}
 throw new Error('Unsupported operation');
}
export function subscribe(onUpdate:()=>void,onStatus:(s:string)=>void){
 let debounceTimer: any = null;
 const debouncedUpdate = () => {
  if(debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
   onUpdate();
  }, 400);
 };
 if(DEMO){window.addEventListener('astra-update',debouncedUpdate);onStatus('Demo ready');return()=>{if(debounceTimer) clearTimeout(debounceTimer);window.removeEventListener('astra-update',debouncedUpdate)}}
 try{
  const stream=new EventSource(BASE+'/api/v1/stream',{withCredentials:true});
  stream.addEventListener('update',debouncedUpdate);
  stream.onopen=()=>onStatus('Connected');
  stream.onerror=()=>{
   onStatus('Local fallback active');
   window.addEventListener('astra-update',debouncedUpdate);
  };
  return()=>{if(debounceTimer) clearTimeout(debounceTimer);stream.close();window.removeEventListener('astra-update',debouncedUpdate)}
 }catch{
  window.addEventListener('astra-update',debouncedUpdate);
  onStatus('Demo ready');
  return()=>{if(debounceTimer) clearTimeout(debounceTimer);window.removeEventListener('astra-update',debouncedUpdate)};
 }
}
export async function health(){
 if(DEMO)return {status:'demo',database:'Browser demo storage',modelMode:'mock',stream:'Manual simulation',queue:'Not connected'};
 try{
  const r=await fetch(BASE+'/health',{credentials:'include'});
  if(!r.ok)throw new Error('Health check failed');
  return r.json();
 }catch{
  return {status:'demo',database:'Browser demo storage (backend offline)',modelMode:'mock',stream:'Manual simulation',queue:'Not connected'};
 }
}

function responseDemo(parts:string[],method:string,b:any):any{
 const [,kind,rid,action]=parts;
 if(kind==='policy'){if(method==='GET'){const {id:unused,...p}=state.containment_policy[0];return p;}const {id:unused,...safe}=b;state.containment_policy=[{id:'demo-policy',...safe}];audit('containment.policy_changed','demo-policy');save();return safe}
 if(kind==='capsules'){const c=state.capsules.find(c=>c.id===rid);if(!c)throw new Error('Capsule not found');return c}
 if(kind==='incidents'){
  if(!rid)return state.incidents;
  const i=state.incidents.find(i=>i.id===rid);if(!i)throw new Error('Incident not found');
  if(method==='GET')return {...i,actions:state.response_actions.filter(a=>a.incidentId===rid)};
  if(!b.reason||b.reason.length<5)throw new Error('Provide a reason with at least five characters');
  if(action==='approve'){if(i.state==='quarantined')return i;if(!['advisory','pending_approval'].includes(i.state))throw new Error('Incident cannot be approved');i.state='quarantined'}
  else if(action==='release'){if(i.state==='released')return i;if(i.state!=='quarantined')throw new Error('Endpoint is not quarantined');i.state='released'}
  else if(action==='escalate'){if(i.escalated)return i;i.escalated=true}
  else throw new Error('Unknown response action');
  state.response_actions.unshift({id:id(),incidentId:rid,action:action==='approve'?'quarantine':action,reason:b.reason,timestamp:new Date().toISOString(),result:{status:'succeeded',adapter:'browser-mock',simulated:true,managementChannels:i.policySnapshot.managementChannels,detail:action==='approve'?'Simulated isolation; Astra, SOC, and EDR management channels remain available.':action==='release'?'Simulated network isolation release.':'Escalated to SOC management queue.'}});
  audit('containment.'+action,rid);save();return i;
 }
 throw new Error('Unknown response resource');
}
function demoIncident(ev:Item,entity:Item){
 const p=state.containment_policy[0];
 for(const c of state.capsules){if(c.manifest.triggerTime<=ev.timestamp&&ev.timestamp<=c.manifest.collectUntil&&c.manifest.incidentId&&state.incidents.find(i=>i.id===c.manifest.incidentId)?.entityId===entity.id)c.evidence.push({sourceId:ev.id,phase:'post_event',snapshot:structuredClone(ev),hash:'Browser demo snapshot'})}
 if(ev.riskScore<p.criticalThreshold)return;
 const old=state.incidents.find(i=>i.device===ev.device&&i.state!=='released');if(old){old.riskEvolution.push({eventId:ev.id,timestamp:ev.timestamp,riskScore:ev.riskScore});return}
 const gates={threshold:true,confidence:.86>=p.minimumConfidence,consensus:4>=p.minimumConsensus,ruleCorroboration:!p.requireRuleCorroboration||state.rules.some(r=>r.enabled&&r.feature==='sensitive_files'&&r.threshold<=ev.sensitiveFiles)};
 const iid=id(),cid=id(),auto=p.mode==='automatic_critical'&&Object.values(gates).every(Boolean);
 const incident={id:iid,entityId:entity.id,title:'Critical insider-risk response',priority:'P1',state:auto?'quarantined':p.mode==='advisory'?'advisory':'pending_approval',device:ev.device,entityName:entity.name,riskScore:ev.riskScore,confidence:.86,consensus:4,triggerEventId:ev.id,policySnapshot:structuredClone(p),gates,corroboratingRules:state.rules.filter(r=>r.enabled&&r.feature==='sensitive_files').map(r=>r.name),riskEvolution:[{eventId:ev.id,timestamp:ev.timestamp,riskScore:ev.riskScore}],capsuleId:cid,simulated:true};
 state.incidents.unshift(incident);state.capsules.push({id:cid,manifest:{incidentId:iid,triggerTime:ev.timestamp,collectUntil:new Date(Date.now()+p.postWindowMinutes*60000).toISOString(),categories:p.categories,predictions:structuredClone(entity.predictions||[]),synthetic:true},manifestHash:'Browser demo: immutable evidence guarantees require PostgreSQL',integrityVerified:false,status:'collecting',evidence:state.events.filter(e=>e.entityId===entity.id&&new Date(e.timestamp).getTime()>=Date.now()-p.preWindowMinutes*60000).map(e=>({sourceId:e.id,phase:e.id===ev.id?'trigger':'pre_event',snapshot:structuredClone(e),hash:'Browser demo snapshot'})),seal:null});
 if(auto)state.response_actions.push({id:id(),incidentId:iid,action:'quarantine',reason:'All configured automatic gates met',timestamp:ev.timestamp,result:{status:'succeeded',simulated:true,detail:'Simulated quarantine with management access preserved'}});
 state.notifications.unshift({id:id(),title:'P1 critical incident',body:entity.name,incidentId:iid,read:false,createdAt:ev.timestamp});audit('containment.policy_evaluated',iid);
}
