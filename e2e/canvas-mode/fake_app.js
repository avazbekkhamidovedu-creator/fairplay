(() => {
  const KEY = %KEY%, DEVICE = %DEVICE%;
  const st = { locked: false, exam: null, timer: null, ts: 0 };
  window.__fp = st;
  window.__native = [];
  async function hmac(msg) {
    const k = await crypto.subtle.importKey('raw', new TextEncoder().encode(KEY), {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
    const sig = await crypto.subtle.sign('HMAC', k, new TextEncoder().encode(msg));
    return [...new Uint8Array(sig)].map(b => b.toString(16).padStart(2,'0')).join('');
  }
  const send = (d) => window.dispatchEvent(new CustomEvent('proctora-native', { detail: d }));
  async function beat() {
    const e = st.exam; if (!e) return;
    st.ts = Math.max(Date.now(), st.ts + 1);
    const checks = 'vm=0,disp=1,mir=0,tap=1,inp=1,proc=0';
    const sig = await hmac(`beat|${e.attempt_id}|${e.session}|${DEVICE}|${st.ts}|${st.locked?1:0}|${checks}`);
    await fetch(`/api/student/attempts/${e.attempt_id}/app-beat`, { method:'POST',
      headers: {'Content-Type':'application/json', Authorization:`Bearer ${e.token}`, 'X-FairPlay-Session': e.session},
      body: JSON.stringify({session:e.session, device_id:DEVICE, ts:st.ts, locked:st.locked, checks, signature:sig}) });
  }
  function setExam(m) {
    if (!m.session) return;
    st.exam = { attempt_id: m.attempt_id, token: m.token, session: m.session };
    if (!st.timer) st.timer = setInterval(beat, 2000);
    beat();
  }
  async function unlock() {
    const e = st.exam;
    if (e) {
      const r = await fetch(`/api/student/attempts/${e.attempt_id}`, { headers: {Authorization:`Bearer ${e.token}`, 'X-FairPlay-Session': e.session} });
      const j = await r.json().catch(() => ({}));
      if (!(r.status === 404 || r.status === 409 || j.status === 'submitted')) { send({type:'unlockPending', reason:'not_submitted'}); return; }
    }
    st.locked = false; st.unlockedAfterSubmit = true; beat(); send({type:'unlocked'});
  }
  window.webkit = { messageHandlers: { proctora: { postMessage: async (m) => {
    window.__native.push(JSON.parse(JSON.stringify(m)));
    if (m.action === 'hello') send({type:'state', locked: st.locked, trusted: true, version: '0.5', app_version: %VERSION%, platform: 'mac'});
    else if (m.action === 'attest') send({type:'attestation', nonce:m.nonce, device_id:DEVICE, signature: await hmac(`start|${m.nonce}|${DEVICE}`), version:%VERSION%, build:'release'});
    else if (m.action === 'session') setExam(m);
    else if (m.action === 'lock') { if (m.session) setExam(m); st.locked = true; beat(); send({type:'locked'}); }
    else if (m.action === 'unlock') unlock();
    else if (m.action === 'preflight') send({type:'preflight', problems: [], checks: [], platform: 'mac', app_version: %VERSION%, apps_to_close: []});
  } } } };
})();
