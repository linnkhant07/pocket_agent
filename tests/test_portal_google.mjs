import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {webcrypto} from 'node:crypto';
import assert from 'node:assert/strict';
import test from 'node:test';

const script = readFileSync(new URL('../src/pocket_agent/ui/static/app.js', import.meta.url), 'utf8');
class FakeSocket {
  constructor(url) { this.url=url; this.sent=[]; this.readyState=1; this.handlers={}; FakeSocket.last=this; }
  addEventListener(event,handler){this.handlers[event]=handler;}
  send(data){this.sent.push(JSON.parse(data));}
  close(){this.readyState=3;}
  emit(event){return this.handlers.message({data:JSON.stringify(event)});}
}
function environment(path='/', search='', flow=null, {session=null, stored={}}={}) {
  const elements = new Map(), storage = new Map(), requests = [], controls = new Map(), labels = new Map();
  const local = new Map(Object.entries(stored));
  function element(tag='') {
    return {tag,value:'',hidden:true,disabled:false,dataset:{},handlers:{},textContent:'',children:[],
      addEventListener(event,handler){this.handlers[event]=handler;},
      replaceChildren(...nodes){this.children=nodes;},append(...nodes){this.children.push(...nodes);},
      setAttribute(){},reset(){},focus(){},get checked(){return Boolean(this._checked);},set checked(v){this._checked=v;},
      elements:{namedItem(){return element();}}};
  }
  if (flow) storage.set('pocketGoogleFlow',JSON.stringify(flow));
  const get = id => { if (!elements.has(id)) elements.set(id,element()); return elements.get(id); };
  get('profileForm').elements = {
    namedItem(name) {
      if (!controls.has(name)) {
        const control=element(), label=element(); control.name=name; control.closest=()=>label;
        controls.set(name,control); labels.set(name,label);
      }
      return controls.get(name);
    },
    [Symbol.iterator](){return controls.values();},
  };
  const timers = [];
  const location = {pathname:path,search,hash:'',host:'127.0.0.1:8000',assign(url){this.assigned=url;}};
  const context = {
    document:{getElementById:get,createElement:element,createTextNode:x=>x},
    window:{location},history:{replaceState(a,b,url){location.pathname=url;location.search='';location.hash='';}},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    localStorage:{getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v)},
    WebSocket:FakeSocket,
    crypto:webcrypto, TextEncoder, btoa:s=>Buffer.from(s,'binary').toString('base64'),
    URLSearchParams,URL,FormData,console,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:id=>{if(id)timers[id-1]=null;},
    fetch:async(path,options={})=>{
      requests.push({path,...options});
      if (path==='/api/session') return session ? {ok:true,status:200,json:async()=>({access_token:session})}
        : {ok:false,status:401,json:async()=>({detail:'Sign in to continue.'})};
      const ready = {key_saved:true,key_from_env:false,model:'provider-model',model_from_env:false};
      if (path==='/api/ai') return {ok:true,status:200,json:async()=>({gpt:ready,claude:ready})};
      const result = path==='/api/auth/google/start' ? {url:'https://project.test/auth/v1/authorize?provider=google'}
        : path==='/api/auth/google/exchange' ? {access_token:'test-access'}
        : {profile:null,answers:[],runs:[],questions:[],user:{email:'person@gmail.com'}};
      return {ok:true,status:200,json:async()=>result};
    },
  };
  return {context,get,location,requests,storage,local,controls,labels,timers};
}
async function load(env) {
  runInNewContext(script,env.context);
  for (let i=0;i<20;i++) await new Promise(r=>setImmediate(r));
}

test('Google button generates per-tab PKCE and state without sending email',async()=>{
  const env=environment(); await load(env);
  assert.deepEqual(env.requests.map(r=>r.path),['/api/session']);
  assert.equal(env.get('workspace').hidden,true);
  env.requests.length=0;
  await env.get('googleLogin').handlers.click();
  assert.equal(env.requests.length,1);
  assert.equal(env.requests[0].path,'/api/auth/google/start');
  const request=JSON.parse(env.requests[0].body), flow=JSON.parse(env.storage.get('pocketGoogleFlow'));
  const digest=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(flow.verifier))).toString('base64url');
  assert.equal(request.challenge,digest); assert.equal(request.state,flow.state);
  assert.equal(env.location.assigned,'https://project.test/auth/v1/authorize?provider=google');
});

test('Google callback removes code and verifier before finishing authenticated account load',async()=>{
  const env=environment('/auth/callback','?code=one-use-code&pa_state=state',{state:'state',verifier:'v'.repeat(43),created:Date.now()});
  await load(env);
  assert.equal(env.location.pathname,'/'); assert.equal(env.location.search,'');
  assert.equal(env.storage.has('pocketGoogleFlow'),false);
  assert.equal(env.requests[0].path,'/api/auth/google/exchange');
  assert.equal(JSON.parse(env.requests[0].body).code,'one-use-code');
  assert.equal(env.requests[1].headers.Authorization,'Bearer test-access');
  assert.equal(env.get('workspace').hidden,false);
});

test('Wrong-state and expired callbacks never exchange credentials',async()=>{
  for (const flow of [{state:'other',verifier:'v'.repeat(43),created:Date.now()},
                      {state:'state',verifier:'v'.repeat(43),created:Date.now()-700000}]) {
    const env=environment('/auth/callback','?code=one-use-code&pa_state=state',flow);
    await load(env);
    assert.equal(env.requests.length,0); assert.equal(env.storage.has('pocketGoogleFlow'),false);
    assert.match(env.get('notice').textContent,/expired|another tab/);
  }
});

test('Unsolicited callback cannot sign the browser into another account',async()=>{
  const env=environment('/auth/callback','?code=attacker-code&pa_state=unknown');
  await load(env);
  assert.equal(env.requests.length,0); assert.equal(env.location.search,'');
});

test('Resume is the first step; uploading autosaves and hides known questions',async()=>{
  const env=environment(); await load(env);
  env.requests.length=0;
  env.get('resumeFile').files=[new File(['%PDF-fictional'],'resume.pdf',{type:'application/pdf'})];
  env.context.fetch=async(path,options)=>{
    env.requests.push({path,...options});
    assert.equal(path,'/api/account/resume');
    assert.equal(options.body.get('expected_revision'),'0');
    return {ok:true,status:200,json:async()=>({profile:{revision:1,data:{
      name:'Alex Rivera',email:'alex@resume.test',education:[{school:'Example University',degree:'BS'}],
      skills:['Python'],resume_object:'uid/resume.pdf',preferences:{roles:[]},
      onboarding:{missing_fields:['citizenship.authorized_to_work','citizenship.requires_sponsorship']},
    }},warnings:[]})};
  };
  await env.get('resumeFile').handlers.change();
  assert.equal(env.requests.length,1); // No prerequisite profile-save call.
  assert.equal(env.requests[0].path,'/api/account/resume');
  assert.equal(env.get('resumeFile').disabled,false);
  assert.equal(env.get('resumeFile').value,'');
  assert.equal(env.controls.get('name').value,'Alex Rivera');
  assert.equal(env.controls.get('school').value,'Example University');
  assert.equal(env.labels.get('name').hidden,true);
  assert.equal(env.labels.get('authorized_us').hidden,false);
  assert.equal(env.labels.get('requires_sponsorship').hidden,false);
  assert.equal(env.get('apply').hidden,true);
  env.get('editExtracted').handlers.click();
  assert.equal(env.labels.get('name').hidden,false);
});

function inputs(node) {
  return (node.children||[]).flatMap(child => typeof child==='object' ? [...(child.tag==='input'?[child]:[]),...inputs(child)] : []);
}
async function signedInWithQuestion(answers=[]) {
  const env=environment(); await load(env);
  env.context.fetch=async(path,options={})=>{
    env.requests.push({path,...options});
    const result = path==='/api/account' ? {profile:null,answers,runs:[],user:{email:'person@gmail.com'},
      questions:[{key:'gender',label:'Gender',sensitive:true}]} : {ok:true};
    return {ok:true,status:200,json:async()=>result};
  };
  await env.get('reload').handlers.click(); env.requests.length=0;
  return env;
}
async function runTimers(env) {
  for (const fn of env.timers.splice(0)) if (fn) await fn();
}

test('Answers autosave the latest typed value and never send always_use',async()=>{
  const env=await signedInWithQuestion();
  const [input]=inputs(env.get('questionList'));
  for (const value of ['P','Pre','Prefer not to say']) { input.value=value; input.handlers.input(); }
  await runTimers(env);
  assert.equal(env.requests.length,1);
  assert.equal(env.requests[0].method,'PUT');
  assert.equal(env.requests[0].path,'/api/account/answers/gender');
  assert.deepEqual(JSON.parse(env.requests[0].body),{value:'Prefer not to say',label:'Gender'});
  input.value='  '; input.handlers.input(); await input.handlers.blur();
  assert.equal(env.requests.length,2);
  assert.equal(env.requests[1].method,'DELETE');
  await runTimers(env);
  assert.equal(env.requests.length,2);
});

test('A value typed while a save is in flight is sent after it, in order',async()=>{
  const env=await signedInWithQuestion();
  let release; const gate=new Promise(r=>{release=r;});
  const fetch=env.context.fetch;
  env.context.fetch=async(path,options)=>{ if (env.requests.length===0) { env.requests.push({path,...options}); await gate; return {ok:true,status:200,json:async()=>({ok:true})}; } return fetch(path,options); };
  const [input]=inputs(env.get('questionList'));
  input.value='first'; input.handlers.input(); const first=input.handlers.blur();
  input.value='second'; input.handlers.input(); await input.handlers.blur();
  assert.equal(env.requests.length,1);
  release(); await first;
  assert.deepEqual(env.requests.map(r=>JSON.parse(r.body).value),['first','second']);
});

test('Answers learned during fills are editable with the same autosave',async()=>{
  const env=await signedInWithQuestion([{key:'learned_key',label:'Learned',value:'old',always_use:true}]);
  const [, learned]=inputs(env.get('questionList'));
  assert.equal(learned.value,'old');
  learned.value='new'; learned.handlers.input(); await runTimers(env);
  assert.equal(env.requests[0].path,'/api/account/answers/learned_key');
  assert.deepEqual(JSON.parse(env.requests[0].body),{value:'new',label:'Learned'});
});

test('Reload restores the Keychain session without a new Google sign-in, and keeps no token in storage',async()=>{
  const env=environment('/','',null,{session:'from-keychain'}); await load(env);
  assert.deepEqual(env.requests.map(r=>r.path),['/api/session','/api/account','/api/ai']);
  assert.equal(env.requests[1].headers.Authorization,'Bearer from-keychain');
  assert.equal(env.get('workspace').hidden,false);
  assert.equal(env.get('identity').textContent,'person@gmail.com');
  assert.equal(FakeSocket.last.url,'ws://127.0.0.1:8000/ws');
  assert.deepEqual([...env.storage.keys(), ...env.local.keys()],[]);
});

test('An expired access token is renewed once from the Keychain session, then the request is retried',async()=>{
  const env=environment('/','',null,{session:'first'}); await load(env);
  let account=0;
  env.context.fetch=async(path,options={})=>{
    env.requests.push({path,...options});
    if (path==='/api/session') return {ok:true,status:200,json:async()=>({access_token:'second'})};
    account+=1;
    return account===1 ? {ok:false,status:401,json:async()=>({detail:'Your session has expired.'})}
      : {ok:true,status:200,json:async()=>({profile:null,answers:[],runs:[],questions:[],user:{email:'person@gmail.com'}})};
  };
  env.requests.length=0;
  await env.get('reload').handlers.click();
  assert.deepEqual(env.requests.map(r=>[r.path,r.headers.Authorization]),
    [['/api/account','Bearer first'],['/api/session','Bearer first'],['/api/account','Bearer second']]);
  assert.equal(env.get('workspace').hidden,false);
});

test('Fill sends the URL and remembered model, answers an inline question, and returns to idle on done',async()=>{
  const env=environment('/','',null,{session:'from-keychain',stored:{pocketModel:'claude'}}); await load(env);
  const socket=FakeSocket.last;
  assert.equal(env.get('model').value,'claude');
  env.get('jobUrl').value='https://jobs.example.test/apply';
  env.get('applyForm').handlers.submit({preventDefault(){}});
  assert.deepEqual(socket.sent,[{type:'start',url:'https://jobs.example.test/apply',model:'claude'}]);
  assert.equal(env.get('fill').disabled,true);

  socket.emit({type:'ask',key:'gender',label:'Gender',question:'Gender?',kind:'dropdown',options:['Woman','Man'],sensitive:true});
  assert.equal(env.get('askCard').hidden,false);
  assert.equal(env.get('askRememberRow').hidden,false);
  assert.equal(env.get('askRemember').checked,false);
  const [control]=env.get('askControl').children;
  assert.equal(control.tag,'select');
  control.value='Woman';
  env.get('askSend').handlers.click();
  assert.deepEqual(socket.sent[1],{type:'answer',value:'Woman',skip:false,always_use:false});
  assert.equal(env.get('askCard').hidden,true);

  socket.emit({type:'ask',key:'referrer',label:'Referrer',question:'Who referred you?',kind:'text',options:[],sensitive:false});
  assert.equal(env.get('askRememberRow').hidden,true);
  env.get('askSkip').handlers.click();
  assert.deepEqual(socket.sent[2],{type:'answer',value:'',skip:true,always_use:false});

  await socket.emit({type:'done',status:'review',unresolved:[]});
  assert.equal(env.get('fill').disabled,false);
  assert.equal(env.get('fillResult').dataset.status,'review');
  assert.match(env.get('fillResult').textContent,/Submit yourself/);
});

test('Fill refuses plain HTTP before sending anything',async()=>{
  const env=environment('/','',null,{session:'from-keychain'}); await load(env);
  env.get('jobUrl').value='http://jobs.example.test/apply';
  env.get('applyForm').handlers.submit({preventDefault(){}});
  assert.deepEqual(FakeSocket.last.sent,[]);
  assert.match(env.get('notice').textContent,/HTTPS/);
});

function texts(node) {
  if (typeof node !== 'object' || node === null) return [];
  return [...(node.textContent ? [node.textContent] : []), ...(node.children || []).flatMap(texts)];
}
async function signedInWith(account) {
  const env=environment('/','',null,{session:'from-keychain'});
  env.context.fetch=async(path,options={})=>{
    env.requests.push({path,...options});
    if (path==='/api/session') return {ok:true,status:200,json:async()=>({access_token:'from-keychain'})};
    if (path==='/api/ai') return {ok:true,status:200,json:async()=>account.ai};
    if (path.startsWith('/api/ai/')) return {ok:true,status:200,json:async()=>account.afterSave};
    return {ok:true,status:200,json:async()=>({profile:{revision:1,data:account.profile||{}},answers:[],runs:account.runs||[],questions:[],user:{email:'person@gmail.com'}})};
  };
  await load(env);
  return env;
}

test('Resume details are grouped into readable sections instead of dotted keys',async()=>{
  const env=await signedInWith({profile:{resume_object:'uid/x.pdf',name:'Alex Rivera',email:'alex@resume.test',
    location:{city:'Austin',region:'TX'},links:{github:'https://github.com/alex'},
    education:[{school:'Example University',degree:'BS',field:'Computer Science',graduation:'2026-05'}],
    experience:[{title:'Intern',org:'Contoso',start:'2025-05',end:'2025-08',highlights:['Built a service']}],
    skills:['Python','Go']}});
  const all=texts(env.get('extractedSummary'));
  assert.ok(all.includes('Imported from your resume: contact, links, education, experience, skills.'));
  for (const expected of ['Contact','Alex Rivera','Austin, TX','GitHub','https://github.com/alex','BS in Computer Science, Example University',
                          'Intern at Contoso','2025-05 to 2025-08','Built a service','Python','Go']) assert.ok(all.includes(expected), expected);
  assert.ok(!all.some(line => /skills · \d|links · /.test(line)), all.join('|'));
});

test('History shows one row per application with its latest result',async()=>{
  const url='https://jobs.example.test/apply';
  const env=await signedInWith({runs:[
    {url,status:'review',created_at:'2026-11-16T13:42:58Z',unresolved:[]},
    {url,status:'stopped',created_at:'2026-11-16T10:49:57Z',unresolved:[]},
    {url:'https://other.example.test/job',status:'blocked',created_at:'2026-11-15T10:00:00Z',unresolved:[]}]});
  const rows=env.get('runList').children;
  assert.equal(rows.length,2);
  assert.match(rows[0].children[0].textContent,/^Ready for review · .* · filled 2 times$/);
  assert.equal(rows[0].children[1].textContent,url);
  assert.match(rows[1].children[0].textContent,/^Needed you · /);
});

test('AI drafting asks for a key and model, saves them, and never echoes the key',async()=>{
  const off={gpt:{key_saved:false,key_from_env:false,model:'',model_from_env:false},claude:{key_saved:false,key_from_env:false,model:'',model_from_env:false}};
  const env=await signedInWith({ai:off,afterSave:{...off,gpt:{key_saved:true,key_from_env:false,model:'provider-model',model_from_env:false}}});
  assert.equal(env.get('aiSetup').hidden,true);
  env.get('model').value='gpt'; env.get('model').handlers.change();
  assert.equal(env.get('aiSetup').hidden,false);
  assert.match(env.get('aiStatus').textContent,/Add your OpenAI API key/);
  env.get('jobUrl').value='https://jobs.example.test/apply';
  env.get('applyForm').handlers.submit({preventDefault(){}});
  assert.deepEqual(FakeSocket.last.sent,[]);
  assert.match(env.get('notice').textContent,/AI drafting settings/);

  env.get('aiKey').value='sk-test-0123456789'; env.get('aiModel').value='provider-model';
  env.requests.length=0;
  await env.get('aiSave').handlers.click();
  assert.equal(env.requests[0].path,'/api/ai/gpt');
  assert.deepEqual(JSON.parse(env.requests[0].body),{api_key:'sk-test-0123456789',model:'provider-model'});
  assert.equal(env.get('aiKey').value,'');
  assert.match(env.get('aiKey').placeholder,/Saved/);
  assert.equal(env.get('aiForget').hidden,false);
  assert.match(env.get('aiStatus').textContent,/OpenAI is ready/);
  env.get('applyForm').handlers.submit({preventDefault(){}});
  assert.equal(FakeSocket.last.sent[0].model,'gpt');
});
