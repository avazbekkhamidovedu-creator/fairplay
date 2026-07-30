(function () {
  'use strict';
  var app = document.getElementById('workspace');
  if (!app) return;
  var body = document.getElementById('ws-body'), drawer = document.getElementById('ws-drawer');
  var source = document.getElementById('workspace-source'), monacoMount = document.getElementById('monaco-editor');
  var editor, layoutKey = 'dkuoj-ws-layout', splitKey = 'dkuoj-ws-split-';
  function setLayout(mode) {
    body.classList.toggle('layout-lr', mode === 'lr'); body.classList.toggle('layout-tb', mode === 'tb');
    document.getElementById('layout-mode').textContent = mode === 'lr' ? '左右' : '上下';
    localStorage.setItem(layoutKey, mode); applySplit();
  }
  function applySplit() { var mode = body.classList.contains('layout-tb') ? 'tb' : 'lr', pct = localStorage.getItem(splitKey + mode) || '50'; body.style.setProperty('--workspace-split', pct + '%'); document.getElementById('pane-left').style.flexBasis = pct + '%'; }
  setLayout(localStorage.getItem(layoutKey) || (innerWidth < 800 ? 'tb' : 'lr'));
  document.getElementById('layout-toggle').onclick = function () { setLayout(body.classList.contains('layout-lr') ? 'tb' : 'lr'); };
  document.getElementById('btn-overview').onclick = function () { drawer.classList.toggle('open'); document.getElementById('drawer-backdrop').classList.toggle('open'); };
  document.getElementById('drawer-close').onclick = function () { drawer.classList.remove('open'); document.getElementById('drawer-backdrop').classList.remove('open'); };
  document.getElementById('drawer-backdrop').onclick = document.getElementById('drawer-close').onclick;
  document.querySelectorAll('[data-tab]').forEach(function (tab) { tab.onclick = function () { document.querySelectorAll('[data-tab]').forEach(function (x) { x.classList.remove('active'); }); document.querySelectorAll('.ws-tab-panel').forEach(function (x) { x.classList.remove('active'); }); tab.classList.add('active'); document.getElementById('panel-' + tab.dataset.tab).classList.add('active'); }; });
  var drag = false, splitter = document.getElementById('ws-splitter');
  splitter.addEventListener('pointerdown', function (e) { drag = true; splitter.setPointerCapture(e.pointerId); });
  splitter.addEventListener('pointermove', function (e) { if (!drag) return; var tb = body.classList.contains('layout-tb'), rect = body.getBoundingClientRect(), p = tb ? (e.clientY - rect.top) / rect.height * 100 : (e.clientX - rect.left) / rect.width * 100; p = Math.max(22, Math.min(78, p)); document.getElementById('pane-left').style.flexBasis = p + '%'; localStorage.setItem(splitKey + (tb ? 'tb' : 'lr'), p); });
  splitter.addEventListener('pointerup', function () { drag = false; });
  var items = Array.prototype.slice.call(document.querySelectorAll('.drawer-problem')), page = 0, perPage = 20;
  function filterItems() { var q = (document.getElementById('drawer-q') || {}).value || '', type = (document.getElementById('fp-type') || {}).value || '', status = (document.getElementById('fp-status') || {}).value || ''; var visible = items.filter(function (x) { return (!q || x.textContent.toLowerCase().indexOf(q.toLowerCase()) >= 0) && (!type || x.dataset.type === type) && (!status || !x.dataset.status || x.dataset.status === status); }); items.forEach(function (x) { x.hidden = visible.indexOf(x) < 0 || Math.floor(visible.indexOf(x) / perPage) !== page; }); var info = document.getElementById('drawer-page-info'); if (info) info.textContent = 'Page ' + (page + 1) + ' / ' + Math.max(1, Math.ceil(visible.length / perPage)); }
  ['drawer-q','fp-type','fp-status','fp-diff','fp-ac-min','fp-ac-max'].forEach(function (id) { var el=document.getElementById(id); if(el) el.addEventListener('input', function(){page=0;filterItems();}); });
  ['drawer-page-prev','drawer-page-next'].forEach(function(id){var el=document.getElementById(id);if(el)el.onclick=function(){page=Math.max(0,page+(id.endsWith('next')?1:-1));filterItems();};}); filterItems();
  var current = document.querySelector('.drawer-problem.active'), idx = items.indexOf(current); document.getElementById('btn-prev').onclick = function(){ if(idx>0) location.href=items[idx-1].href;}; document.getElementById('btn-next').onclick = function(){if(idx>=0&&idx+1<items.length)location.href=items[idx+1].href;};
  function value() { return editor ? editor.getValue() : (source ? source.value : ''); } function setValue(v) { if(editor) editor.setValue(v); else if(source) source.value=v; }
  if (source) { var key='dkuoj-ws-draft-'+app.dataset.problem, saved=localStorage.getItem(key); if(saved && !source.value) source.value=saved; source.addEventListener('input',function(){localStorage.setItem(key,source.value);});
    window.require = { paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs' } }; var loader=document.createElement('script'); loader.src='https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs/loader.js'; loader.onload=function(){window.require(['vs/editor/editor.main'],function(){monacoMount.style.height='100%'; editor=monaco.editor.create(monacoMount,{value:source.value,language:'cpp',theme:'vs-dark',automaticLayout:true,minimap:{enabled:false}});source.style.display='none';editor.onDidChangeModelContent(function(){source.value=editor.getValue();localStorage.setItem(key,source.value);});editor.onDidChangeCursorPosition(function(e){document.getElementById('cursor-status').textContent='Ln '+e.position.lineNumber+', Col '+e.position.column;});});}; loader.onerror=function(){source.classList.add('ace-mock');}; document.head.appendChild(loader);
    document.getElementById('workspace-submit-form').addEventListener('submit',function(){source.value=value();});
    document.getElementById('btn-submit').onclick=function(){document.getElementById('workspace-submit-form').requestSubmit();};
    document.getElementById('btn-run').onclick=function(){var fd=new FormData();fd.append('source',value());fd.append('language',document.getElementById('id_language').value);fd.append('input',document.getElementById('workspace-input').value);fd.append('csrfmiddlewaretoken',document.querySelector('[name=csrfmiddlewaretoken]').value);fetch(app.dataset.runUrl,{method:'POST',body:fd,credentials:'same-origin'}).then(function(r){return r.json();}).then(function(d){var msg=document.getElementById('run-message');msg.textContent=d.error||'Run queued; polling isolated sandbox…';if(!d.submission_url)return;var t=setInterval(function(){fetch(d.submission_url,{credentials:'same-origin'}).then(function(r){return r.json();}).then(function(s){document.getElementById('workspace-output').textContent=(s.output||s.error||'') ;msg.textContent=s.result||s.status;if(s.status!=='QU'&&s.status!=='P')clearInterval(t);});},1000);}).catch(function(){document.getElementById('run-message').textContent='Run request failed.';});};
  } else { document.getElementById('btn-submit').onclick=function(){document.getElementById('workspace-submit-form').requestSubmit();}; }
  document.getElementById('btn-collapse-bottom') && (document.getElementById('btn-collapse-bottom').onclick=function(){document.getElementById('ide-bottom').classList.toggle('collapsed');});
}());
