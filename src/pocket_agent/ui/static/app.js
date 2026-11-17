'use strict';
const $ = id => document.getElementById(id);
let token = '', profile = {}, revision = 0, savedAnswers = [], showExtracted = false;
const autosaves = new Map();
function notice(message, error = false) { $('notice').textContent = message; $('notice').dataset.error = String(error); }
async function request(path, options) {
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = 'Bearer ' + token;
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...options, headers});
  let body = {};
  try { body = await response.json(); } catch {
    if (response.ok) throw new Error('The server sent an unexpected response. Reload and try again.');
    body = {detail: response.status === 413 ? 'That upload is too large. Choose a PDF of 10 MB or smaller.'
      : 'The server could not complete this request (' + response.status + '). Try again shortly.'};
  }
  return {response, body};
}
// The access token lives only in memory. The server mints a new one from the Keychain session.
async function refreshSession() {
  const {response, body} = await request('/api/session', {method:'POST'});
  token = response.ok && typeof body.access_token === 'string' ? body.access_token : '';
  return token !== '';
}
async function api(path, options = {}) {
  let {response, body} = await request(path, options);
  if (response.status === 401 && token && await refreshSession()) ({response, body} = await request(path, options));
  if (!response.ok) {
    if (response.status === 401) signOut();
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the fields and try again.');
  }
  return body;
}
function bindForm(id, action) {
  $(id).addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.submitter; if (button) button.disabled = true;
    try { await action(event); } catch (error) { notice(error.message, true); }
    finally { if (button) button.disabled = false; }
  });
}
function signOut() {
  for (const record of autosaves.values()) clearTimeout(record.timer);
  autosaves.clear();
  token = ''; profile = {}; revision = 0; savedAnswers = []; showExtracted = false;
  $('workspace').hidden = true; $('auth').hidden = false; $('logout').hidden = true;
  $('questionList').replaceChildren(); $('runList').replaceChildren(); $('profileForm').reset();
  $('extractedSummary').replaceChildren(); $('repeatDetails').replaceChildren();
  $('parseState').textContent = ''; $('identity').textContent = '';
  $('profile').hidden = true; $('answers').hidden = true; $('apply').hidden = true;
  $('resumeState').textContent = 'Upload your resume first. We will fill in the details for you.';
  $('advanced').value = '{}'; $('resumeFile').value = '';
  desk.close();
}
$('logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', {method:'POST'}); notice('You have signed out on this computer.'); }
  catch { notice('Signed out here. Pocket Agent could not confirm the session was revoked.', true); }
  finally { signOut(); }
});
async function finishSignIn(result) {
  token = result.access_token;
  try { await reload(); notice(profile.resume_object ? 'Welcome back. Your resume details are ready.' : 'Start by uploading your resume. We will fill in your details automatically.'); }
  catch (error) {
    // Keep the valid session so a temporary profile-fetch failure can be retried.
    $('logout').hidden = false; $('workspace').hidden = false; $('auth').hidden = true;
    throw error;
  }
}
function randomOAuthValue() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
}
$('googleLogin').addEventListener('click', async () => {
  $('googleLogin').disabled = true;
  try {
    const verifier = randomOAuthValue(), state = randomOAuthValue();
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    const challenge = btoa(String.fromCharCode(...digest)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
    const result = await api('/api/auth/google/start', {method:'POST', body:JSON.stringify({challenge,state})});
    sessionStorage.setItem('pocketGoogleFlow', JSON.stringify({verifier,state,created:Date.now()}));
    window.location.assign(result.url);
  } catch (error) { notice(error.message,true); $('googleLogin').disabled = false; }
});
async function completeGoogleSignIn() {
  if (window.location.pathname !== '/auth/callback') return;
  const params = new URLSearchParams(window.location.search);
  const rejected = params.has('error') || new URLSearchParams(window.location.hash.slice(1)).has('error');
  history.replaceState(null, '', '/');
  try {
    const stored = sessionStorage.getItem('pocketGoogleFlow');
    sessionStorage.removeItem('pocketGoogleFlow');
    const flow = stored ? JSON.parse(stored) : null;
    if (rejected) throw new Error('Google sign-in was cancelled or rejected. Please try again.');
    if (!flow || flow.state !== params.get('pa_state') || Date.now()-flow.created > 600000 || !params.get('code')) {
      throw new Error('Sign-in expired or started in another tab. Click Continue with Google again here.');
    }
    const result = await api('/api/auth/google/exchange', {method:'POST', body:JSON.stringify({code:params.get('code'),verifier:flow.verifier})});
    await finishSignIn(result);
  } catch (error) { notice(error.message,true); }
}
const text = value => (value === null || value === undefined ? '' : String(value).trim());
const joined = (parts, separator = ', ') => parts.map(text).filter(Boolean).join(separator);
const LINK_NAMES = {github:'GitHub', linkedin:'LinkedIn', portfolio:'Portfolio', website:'Website'};
// Each section is a list of entries: a main line, an optional detail line, and optional bullets or chips.
function resumeSections(data) {
  const location = data.location || {};
  const contact = [['Name', data.name], ['Email', data.email], ['Phone', data.phone],
    ['Location', joined([location.city, location.region, location.country])]]
    .filter(([, value]) => text(value)).map(([label, value]) => ({main:text(value), detail:label}));
  const links = Object.entries(data.links || {}).filter(([, url]) => text(url))
    .map(([name, url]) => ({main:text(url), detail:LINK_NAMES[name] || name, url:text(url)}));
  const education = (data.education || []).filter(school => text(school.school) || text(school.degree)).map(school => ({
    main: joined([joined([school.degree, school.field], ' in '), school.school]),
    detail: joined([school.start && school.graduation ? school.start + ' to ' + school.graduation : school.graduation || school.end || school.start,
      school.gpa ? 'GPA ' + school.gpa : ''], ' · ')}));
  const experience = (data.experience || []).filter(job => text(job.title) || text(job.org)).map(job => ({
    main: joined([job.title, job.org], ' at '), detail: joined([job.start, job.end], ' to '),
    bullets: (job.highlights || []).map(text).filter(Boolean)}));
  const skills = (Array.isArray(data.skills) ? data.skills : Object.values(data.skills || {}).flat()).map(text).filter(Boolean);
  return [['Contact', contact], ['Links', links], ['Education', education], ['Experience', experience],
    ['Skills', skills.length ? [{chips:skills}] : []]].filter(([, entries]) => entries.length);
}
function renderResumeSummary() {
  const sections = resumeSections(profile);
  $('extractedSummary').replaceChildren();
  if (!sections.length) return;
  const heading = document.createElement('p');
  heading.textContent = 'Imported from your resume: ' + sections.map(([title]) => title.toLowerCase()).join(', ') + '.';
  const details = document.createElement('details'), summary = document.createElement('summary');
  summary.textContent = 'View extracted details';
  const grid = document.createElement('div'); grid.className = 'resume-sections';
  for (const [title, entries] of sections) {
    const section = document.createElement('section'); section.className = 'resume-section ' + title.toLowerCase();
    const label = document.createElement('h4'); label.textContent = title; section.append(label);
    for (const entry of entries) {
      const item = document.createElement('div'); item.className = 'resume-entry';
      if (entry.chips) {
        for (const skill of entry.chips) { const chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = skill; item.append(chip); }
      } else {
        const main = document.createElement(entry.url ? 'a' : 'p'); main.className = 'entry-main'; main.textContent = entry.main;
        if (entry.url) { try { const url = new URL(entry.url); if (['http:','https:'].includes(url.protocol)) { main.href = url.href; main.target = '_blank'; main.rel = 'noopener noreferrer'; } } catch {} }
        item.append(main);
        if (entry.detail) { const detail = document.createElement('p'); detail.className = 'entry-detail'; detail.textContent = entry.detail; item.append(detail); }
        if (entry.bullets && entry.bullets.length) {
          const list = document.createElement('ul');
          for (const bullet of entry.bullets) { const li = document.createElement('li'); li.textContent = bullet; list.append(li); }
          item.append(list);
        }
      }
      section.append(item);
    }
    grid.append(section);
  }
  details.append(summary, grid);
  $('extractedSummary').append(heading, details);
}
function setField(name, value) { $('profileForm').elements.namedItem(name).value = value || ''; }
function onboardingState() {
  const hasResume = Boolean(profile.resume_object);
  $('profile').hidden = !hasResume; $('answers').hidden = !hasResume;
  const missing = (profile.onboarding || {}).missing_fields || [];
  $('apply').hidden = !hasResume || missing.length > 0;
  renderResumeSummary();
  for (const control of Array.from($('profileForm').elements)) {
    if (!control.name || !control.closest) continue;
    const label = control.closest('label');
    if (label) label.hidden = !showExtracted && Boolean(control.value.trim());
  }
  $('editExtracted').textContent = showExtracted ? 'Show only missing details' : 'Edit extracted details';
}
$('editExtracted').addEventListener('click', () => { showExtracted = !showExtracted; onboardingState(); });
function repeatDetails() {
  $('repeatDetails').replaceChildren();
  const field = (name,label,value,required=false,multiline=false) => {
    const wrapper=document.createElement('label'); wrapper.textContent=label;
    const control=document.createElement(multiline ? 'textarea' : 'input');
    control.name=name; control.value=value || ''; control.required=required;
    wrapper.append(control); $('repeatDetails').append(wrapper);
  };
  (profile.education || []).slice(1).forEach((school,offset) => {
    const index=offset+1;
    for (const [key,label] of [['school','School'],['degree','Degree'],['field','Field of study'],['start','Start date'],['graduation','Graduation date']]) {
      field('education.'+index+'.'+key,'Education '+(index+1)+' · '+label,school[key],['school','degree'].includes(key));
    }
  });
  (profile.experience || []).forEach((job,index) => {
    for (const [key,label] of [['title','Job title'],['org','Employer'],['start','Start date'],['end','End date or Present']]) {
      field('experience.'+index+'.'+key,'Experience '+(index+1)+' · '+label,job[key],['title','org'].includes(key));
    }
    field('experience.'+index+'.highlights','Experience '+(index+1)+' · Accomplishments (one per line)',(job.highlights || []).join('\n'),false,true);
  });
}
function fillProfile() {
  const location = profile.location || {}, education = (profile.education || [])[0] || {};
  for (const name of ['name','email','phone']) setField(name, profile[name]);
  for (const name of ['city','region','country']) setField(name, location[name]);
  for (const name of ['school','degree','graduation']) setField(name, education[name]);
  const citizenship = profile.citizenship || {};
  setField('citizenship_country', citizenship.country);
  setField('authorized_us', Array.isArray(citizenship.authorized_to_work) ? (citizenship.authorized_to_work.includes('US') ? 'yes' : 'no') : '');
  setField('requires_sponsorship', typeof citizenship.requires_sponsorship === 'boolean' ? (citizenship.requires_sponsorship ? 'yes' : 'no') : '');
  setField('permanent_resident_us', Array.isArray(citizenship.permanent_residence) ? (citizenship.permanent_residence.includes('US') ? 'yes' : 'no') : '');
  const skills = profile.skills || [];
  setField('skills', (Array.isArray(skills) ? skills : Object.values(skills).flat()).join(', '));
  const roles = (profile.preferences || {}).roles || [];
  setField('roles', Array.isArray(roles) ? roles.join(', ') : roles);
  repeatDetails();
  $('advanced').value = JSON.stringify(profile, null, 2);
  $('revision').textContent = revision ? 'Saved · revision ' + revision : 'Your profile has not been saved yet.';
  $('resumeState').textContent = profile.resume_object ? 'Resume saved. Upload a newer version to refresh extracted details.' : 'Upload your resume first. We will fill in the details for you.';
  onboardingState();
}
function buildProfile() {
  let data;
  try { data = JSON.parse($('advanced').value); } catch { throw new Error('The advanced profile must contain valid JSON.'); }
  if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('The advanced profile must be a JSON object.');
  const field = name => $('profileForm').elements.namedItem(name).value.trim();
  for (const name of ['name','email','phone']) data[name] = field(name);
  data.location = {...data.location, city:field('city'), region:field('region'), country:field('country')};
  const education = Array.isArray(data.education) ? data.education : [];
  data.education = [{...education[0],school:field('school'),degree:field('degree'),graduation:field('graduation')},...education.slice(1)];
  data.education.slice(1).forEach((school,offset) => {
    for (const key of ['school','degree','field','start','graduation']) {
      const control=$('profileForm').elements.namedItem('education.'+(offset+1)+'.'+key);
      if (control) school[key]=control.value.trim();
    }
  });
  (data.experience || []).forEach((job,index) => {
    for (const key of ['title','org','start','end','highlights']) {
      const control=$('profileForm').elements.namedItem('experience.'+index+'.'+key);
      if (control) job[key]=key==='highlights' ? control.value.split('\n').map(x=>x.trim()).filter(Boolean) : control.value.trim();
    }
  });
  const authorized = field('authorized_us'), sponsorship = field('requires_sponsorship');
  if (!['yes','no'].includes(authorized) || !['yes','no'].includes(sponsorship)) throw new Error('Answer work authorization and sponsorship before saving your profile.');
  const citizenship = {...data.citizenship};
  const withUS = (countries, choice) => [...(Array.isArray(countries) ? countries.filter(country => country !== 'US') : []), ...(choice === 'yes' ? ['US'] : [])];
  citizenship.authorized_to_work = withUS(citizenship.authorized_to_work, authorized);
  citizenship.requires_sponsorship = sponsorship === 'yes';
  citizenship.country = field('citizenship_country');
  const residence = field('permanent_resident_us');
  if (residence) citizenship.permanent_residence = withUS(citizenship.permanent_residence, residence);
  else if (Array.isArray(citizenship.permanent_residence)) citizenship.permanent_residence = citizenship.permanent_residence.filter(country => country !== 'US');
  data.citizenship = citizenship;
  data.skills = field('skills').split(',').map(x => x.trim()).filter(Boolean);
  data.preferences = {...data.preferences, roles:field('roles').split(',').map(x => x.trim()).filter(Boolean)};
  if (profile.resume_object) data.resume_object = profile.resume_object;
  return data;
}
async function saveProfile(data) {
  const row = await api('/api/account/profile', {method:'PUT', body:JSON.stringify({data,expected_revision:revision})});
  profile = row.data; revision = row.revision; fillProfile();
}
bindForm('profileForm', async () => { await saveProfile(buildProfile()); notice('Profile saved.'); });
$('resumeFile').addEventListener('change', async () => {
  const input = $('resumeFile'), file = input.files[0];
  if (!file) return;
  input.disabled = true;
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error('Choose a PDF resume, 10 MB or smaller.');
    const form = new FormData(); form.append('file',file); form.append('expected_revision',String(revision));
    $('parseState').textContent = 'Reading your resume and filling your details…';
    try {
      const uploaded = await api('/api/account/resume', {method:'POST', body:form});
      profile = uploaded.profile.data; revision = uploaded.profile.revision; showExtracted = false;
      fillProfile();
      const warnings = uploaded.warnings || [];
      const message = warnings.length ? warnings.join(' ') : 'Resume saved and details filled. Answer only the remaining questions below.';
      $('parseState').textContent = message; notice(message,warnings.length > 0);
    } catch(error) { $('parseState').textContent = 'Resume import did not finish. Your previous profile is unchanged.'; throw error; }
  } catch(error) { notice(error.message, true); }
  finally { input.disabled = false; input.value = ''; }
});
function autosaveRecord(key, label, status) {
  if (!autosaves.has(key)) autosaves.set(key, {pending:null, inFlight:false, timer:null});
  const record = autosaves.get(key); record.label = label; record.status = status;
  return record;
}
// One request per key at a time; whatever was typed during it is sent next, so the last value typed is the one stored.
async function flushAnswer(key) {
  const record = autosaves.get(key);
  if (!record) return;
  clearTimeout(record.timer); record.timer = null;
  if (record.inFlight || record.pending === null) return;
  const value = record.pending; record.pending = null; record.inFlight = true;
  record.status.textContent = 'Saving…';
  const path = '/api/account/answers/' + encodeURIComponent(key);
  try {
    if (value.trim()) await api(path, {method:'PUT', body:JSON.stringify({value, label:record.label})});
    else await api(path, {method:'DELETE'});
    if (record.pending === null) record.status.textContent = value.trim() ? 'Saved' : 'Removed';
  } catch(error) { record.status.textContent = error.message; }
  finally {
    record.inFlight = false;
    if (autosaves.get(key) === record && record.pending !== null) await flushAnswer(key);
  }
}
function answerInput(key, label, value) {
  const input = document.createElement('input'); input.value = value; input.maxLength = 20000;
  const status = document.createElement('span'); status.className = 'muted'; status.setAttribute('role','status');
  autosaveRecord(key, label, status);
  input.addEventListener('input', () => {
    const record = autosaves.get(key); record.pending = input.value;
    clearTimeout(record.timer); record.timer = setTimeout(() => flushAnswer(key), 600);
  });
  input.addEventListener('blur', () => flushAnswer(key));
  input.addEventListener('change', () => flushAnswer(key));
  return {input, status};
}
function renderQuestions(questions) {
  $('questionList').replaceChildren();
  const entries = new Map(savedAnswers.map(a => [a.key,a]));
  for (const question of questions) {
    const saved = entries.get(question.key), row = document.createElement('div'); row.className = 'question';
    const heading = document.createElement('h3'); heading.textContent = question.label;
    if (question.sensitive) { const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = 'Sensitive · optional'; heading.append(badge); }
    const label = document.createElement('label'); label.textContent = 'Your answer';
    const {input, status} = answerInput(question.key, question.label, saved ? saved.value : '');
    label.append(input); row.append(heading, label, status); $('questionList').append(row);
  }
  const asked = new Set(questions.map(q => q.key));
  const learned = savedAnswers.filter(a => !asked.has(a.key));
  if (learned.length) {
    // Mostly one company's questions: reused automatically when a form asks the same thing, so kept out of the way.
    const box = document.createElement('details'), summary = document.createElement('summary');
    summary.textContent = `Answers from past applications (${learned.length})`;
    const hint = document.createElement('p'); hint.className = 'muted'; hint.textContent = 'Reused automatically when a form asks the same question. Edit one here, or clear it to remove it.';
    box.append(summary, hint);
    for (const answer of learned) {
      const row = document.createElement('div'); row.className = 'question';
      const label = document.createElement('label'); label.textContent = answer.label || answer.key;
      const {input, status} = answerInput(answer.key, answer.label || '', answer.value);
      label.append(input); row.append(label, status); box.append(row);
    }
    $('questionList').append(box);
  }
}
const RUN_STATUS = {review:'Ready for review', blocked:'Needed you', stopped:'Stopped before Review'};
// Runs arrive newest first. One row per application: its latest result and how many times it was filled.
function latestRuns(runs) {
  const byUrl = new Map();
  for (const run of runs) {
    const seen = byUrl.get(run.url);
    if (seen) seen.attempts += 1; else byUrl.set(run.url, {...run, attempts:1});
  }
  return [...byUrl.values()];
}
function renderRuns(runs) {
  $('runList').replaceChildren();
  if (!runs.length) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = 'Your fills will appear here.'; $('runList').append(p); }
  for (const run of latestRuns(runs)) {
    const row = document.createElement('article'); row.className = 'run';
    const status = document.createElement('strong');
    status.textContent = joined([RUN_STATUS[run.status] || run.status, new Date(run.created_at).toLocaleString(),
      run.attempts > 1 ? 'filled ' + run.attempts + ' times' : ''], ' · ');
    row.append(status);
    try { const url = new URL(run.url); if (!['http:','https:'].includes(url.protocol)) throw new Error(); const a = document.createElement('a'); a.href = url.href; a.textContent = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; row.append(a); } catch { const p = document.createElement('p'); p.textContent = String(run.url || ''); row.append(p); }
    if (Array.isArray(run.unresolved) && run.unresolved.length) {
      const label = document.createElement('p'); label.textContent = 'Questions to review:'; const list = document.createElement('ul');
      for (const question of run.unresolved) { const item = document.createElement('li'); item.textContent = typeof question === 'string' ? question : String(question.label || question.key || 'Unanswered question'); list.append(item); }
      row.append(label,list);
    }
    $('runList').append(row);
  }
}
async function reload() {
  const result = await api('/api/account'); profile = result.profile ? result.profile.data : {}; revision = result.profile ? result.profile.revision : 0;
  savedAnswers = result.answers || []; fillProfile(); renderQuestions(result.questions); renderRuns(result.runs || []);
  $('identity').textContent = result.user.email || 'YOUR WORKSPACE'; $('workspace').hidden = false; $('auth').hidden = true; $('logout').hidden = false;
  desk.open();
  if (!aiStatus) loadAI();
}
$('deleteData').addEventListener('click', async () => {
  if (!window.confirm('Delete your profile, resumes, saved answers, and fill history from Pocket Agent? This cannot be undone.')) return;
  $('deleteData').disabled = true;
  try { await api('/api/account', {method:'DELETE'}); signOut(); notice('Your Pocket Agent data was deleted. Your Google sign-in itself remains with Supabase.'); }
  catch (error) { notice(error.message, true); }
  finally { $('deleteData').disabled = false; }
});
$('reload').addEventListener('click', async () => { try { await reload(); notice('Account reloaded.'); } catch(error) { notice(error.message,true); } });
const desk = {socket:null, phase:'idle', asking:null, control:null};
const fillStatus = {review:'Ready for your review. Check the browser window and click Submit yourself.',
  blocked:'The form needs you before it can continue. Finish the step in the browser window, then click Continue on this page.',
  stopped:'The fill stopped before Review. Check the browser window.'};
function logLine(text, error = false) {
  const item = document.createElement('li'); item.textContent = text; if (error) item.dataset.error = 'true';
  $('fillLog').append(item);
}
function setPhase(phase) {
  desk.phase = phase;
  $('fill').disabled = phase !== 'idle'; $('continueFill').disabled = phase !== 'idle';
  $('askCard').hidden = phase !== 'asking';
}
function showAsk(event) {
  desk.asking = event;
  $('askSection').textContent = event.label; $('askQuestion').textContent = event.question;
  let control;
  if (event.options.length) {
    control = document.createElement('select');
    for (const label of event.options) { const option = document.createElement('option'); option.value = label; option.textContent = label; control.append(option); }
  } else {
    control = document.createElement(event.kind === 'textarea' ? 'textarea' : 'input');
    if (event.kind === 'date') control.type = 'date';
  }
  control.id = 'askValue'; control.maxLength = 20000; desk.control = control;
  $('askControl').replaceChildren(control);
  setPhase('asking');
}
function sendAnswer(skip) {
  const value = skip ? '' : desk.control.value;
  desk.socket.send(JSON.stringify({type:'answer', value, skip}));
  logLine(skip ? 'Skipped: ' + desk.asking.label : 'Answered: ' + desk.asking.label);
  desk.asking = null; setPhase('filling');
}
const deskEvents = {
  // A tab that reconnects mid-fill learns about it from the next status or question.
  status: event => { logLine(event.text); if (desk.phase === 'idle') setPhase('filling'); },
  error: event => logLine(event.message, true),
  ask: showAsk,
  done: async event => {
    setPhase('idle');
    $('continueFill').hidden = event.status !== 'blocked';
    const unresolved = event.unresolved.length ? ' Still unanswered: ' + event.unresolved.join(', ') + '.' : '';
    $('fillResult').textContent = fillStatus[event.status] + unresolved; $('fillResult').dataset.status = event.status;
    try { await reload(); } catch (error) { notice(error.message, true); }
  },
};
desk.open = () => {
  if (desk.socket) return;
  const socket = new WebSocket('ws://' + window.location.host + '/ws');
  desk.socket = socket;
  socket.addEventListener('message', message => {
    const event = JSON.parse(message.data);
    if (deskEvents[event.type]) deskEvents[event.type](event);
  });
  socket.addEventListener('close', () => {
    if (desk.socket !== socket) return;
    desk.socket = null; setPhase('idle');
    if (token) setTimeout(desk.open, 1000);
  });
};
desk.close = () => { const socket = desk.socket; desk.socket = null; if (socket) socket.close(); setPhase('idle'); };
function startFill(message) {
  if (!desk.socket || desk.socket.readyState !== 1) throw new Error('Still connecting to Pocket Agent. Try again in a moment.');
  $('fillLog').replaceChildren(); $('fillResult').textContent = ''; $('continueFill').hidden = true;
  setPhase('filling');
  desk.socket.send(JSON.stringify({...message, model:$('model').value}));
}
$('applyForm').addEventListener('submit', event => {
  event.preventDefault();
  try {
    const url = new URL($('jobUrl').value.trim());
    if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Enter an HTTPS application URL without embedded credentials.');
    if ($('model').value !== 'none' && aiStatus && !aiReady($('model').value)) throw new Error('Finish the AI drafting settings, or turn AI drafting off.');
    startFill({type:'start', url:url.href});
  } catch (error) { notice(error.message, true); }
});
$('continueFill').addEventListener('click', () => { try { startFill({type:'continue'}); } catch (error) { notice(error.message, true); } });
$('askSend').addEventListener('click', () => sendAnswer(false));
$('askSkip').addEventListener('click', () => sendAnswer(true));
try { const saved = localStorage.getItem('pocketModel'); if (['none','gpt','claude'].includes(saved)) $('model').value = saved; } catch {}
$('model').addEventListener('change', () => { try { localStorage.setItem('pocketModel', $('model').value); } catch {} renderAI(); });
let aiStatus = null;
const AI_NAMES = {gpt:['OpenAI','OPENAI_API_KEY'], claude:['Anthropic','ANTHROPIC_API_KEY']};
function aiReady(provider) {
  const state = aiStatus && aiStatus[provider];
  return Boolean(state && (state.key_saved || state.key_from_env) && state.model);
}
function renderAI() {
  const provider = $('model').value, state = aiStatus && aiStatus[provider];
  $('aiSetup').hidden = provider === 'none' || !state;
  if ($('aiSetup').hidden) return;
  const [name, variable] = AI_NAMES[provider];
  $('aiKey').value = '';
  $('aiKey').placeholder = state.key_saved ? 'Saved. Paste a new key to replace it.' : 'Paste your ' + name + ' API key';
  $('aiModel').value = state.model; $('aiModel').disabled = state.model_from_env;
  $('aiForget').hidden = !state.key_saved;
  $('aiStatus').textContent = state.key_from_env ? 'Using ' + variable + ' from the environment that started Pocket Agent.'
    : aiReady(provider) ? name + ' is ready.' : 'Add your ' + name + ' API key and a model ID to turn on AI drafting.';
}
async function loadAI() {
  try { aiStatus = await api('/api/ai'); } catch (error) { aiStatus = null; notice(error.message, true); }
  renderAI();
}
$('aiSave').addEventListener('click', async () => {
  const provider = $('model').value, key = $('aiKey').value.trim(), model = $('aiModel').value.trim();
  $('aiSave').disabled = true;
  try {
    if (!model) throw new Error('Enter the model ID to use, from your provider account.');
    aiStatus = await api('/api/ai/' + provider, {method:'PUT', body:JSON.stringify(key ? {api_key:key, model} : {model})});
    renderAI(); notice('AI settings saved.');
  } catch (error) { notice(error.message, true); }
  finally { $('aiSave').disabled = false; }
});
$('aiForget').addEventListener('click', async () => {
  try { aiStatus = await api('/api/ai/' + $('model').value, {method:'DELETE'}); renderAI(); notice('Saved API key removed.'); }
  catch (error) { notice(error.message, true); }
});

async function restoreSession() {
  if (await refreshSession()) await finishSignIn({access_token: token});
}
(window.location.pathname === '/auth/callback' ? completeGoogleSignIn() : restoreSession())
  .catch(error => notice(error.message, true));
