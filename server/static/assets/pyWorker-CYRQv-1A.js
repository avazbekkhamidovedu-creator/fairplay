const X=["js","pyodide_js","pyodide","_pyodide","_pyodide_core","micropip"],Q=`
import sys, builtins, gc, io, traceback, types
_blocked = ${JSON.stringify(X)}
sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "JsFinder"]
for _k in list(sys.modules):
    if _k.split(".")[0] in _blocked:
        sys.modules[_k] = None
for _k in _blocked + ["pyodide.ffi", "pyodide.code", "pyodide.http", "pyodide.webloop"]:
    sys.modules[_k] = None
def _no_audit_hooks(hook):
    raise RuntimeError("sys.addaudithook is not available in the exam")
sys.addaudithook = _no_audit_hooks
_dicts = []
for _m in list(sys.modules.values()):
    if isinstance(_m, types.ModuleType):
        _dicts.append((_m.__dict__, dict(_m.__dict__)))
for _s in (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__):
    _d = getattr(_s, "__dict__", None)
    if isinstance(_d, dict):
        _dicts.append((_d, dict(_d)))
_dicts.append((sys.path_importer_cache, dict(sys.path_importer_cache)))
_dicts.append((sys.modules, dict(sys.modules)))
_lists = [(l, list(l)) for l in (sys.path, sys.meta_path, sys.path_hooks, sys.argv, sys.warnoptions)]
_calls = [(sys.settrace, None), (sys.setprofile, None), (sys.setrecursionlimit, sys.getrecursionlimit())]
if hasattr(sys, "set_int_max_str_digits"):
    _calls.append((sys.set_int_max_str_digits, sys.get_int_max_str_digits()))
(builtins.exec, dict, dict.clear, dict.update, list.clear, list.extend, gc.collect, _dicts, _lists, _calls)
`,Y=`
import sys, traceback
def __fairplay_run(src):
    g = {"__name__": "__main__", "__builtins__": __builtins__}
    try:
        code = compile(src, "main.py", "exec")
    except SyntaxError:
        traceback.print_exc(limit=0)
        return 1
    try:
        exec(code, g)
        return 0
    except SystemExit as e:
        if e.code not in (None, 0):
            if not isinstance(e.code, int):
                print(e.code, file=sys.stderr)
            return 1
        return 0
    except BaseException:
        et, ev, tb = sys.exc_info()
        traceback.print_exception(et, ev, tb.tb_next if tb is not None else None)
        return 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
`,Z=new Set(["/dev","/proc","/sys"]);function O(t){const e=[];for(let s=0;s<t.length;s++){const r=t.get(s);e.push([r.get(0),r.get(1)]),r.destroy()}return e}function G(t){const e=new Map,s=r=>{let i;try{i=t.FS.readdir(r)}catch{return}for(const l of i){if(l==="."||l==="..")continue;const _=r==="/"?`/${l}`:`${r}/${l}`;if(Z.has(_))continue;let f;try{f=t.FS.stat(_)}catch{continue}t.FS.isDir(f.mode)?(e.set(_,"dir"),s(_)):e.set(_,`${Number(f.mtime instanceof Date?f.mtime.getTime():f.mtime)}:${f.size}`)}};return s("/"),e}function tt(t){var s;if(!t||typeof t!="object")return null;const e=t;return typeof e.status=="number"&&(e.name==="Exit"||((s=e.constructor)==null?void 0:s.name)==="Exit")?e.status:null}function et(t){const e=t.runPython(Q,{globals:t.toPy({})}),[s,r,i,l,_,f,h]=[0,1,2,3,4,5,6].map(o=>e.get(o)),a=e.get(7),g=e.get(8),j=e.get(9),J=O(a),z=O(g),A=O(j);a.destroy(),g.destroy(),j.destroy(),e.destroy();const D=G(t),b={write:o=>o.length};function L(){for(const[o,c]of J)i(o),l(o,c);for(const[o,c]of z)_(o),f(o,c);for(const[o,c]of A)o(c)}function H(){let o=!1;const c=G(t),w=[...c.keys()].filter(n=>!D.has(n)).sort((n,p)=>p.length-n.length);for(const n of w)try{c.get(n)==="dir"?t.FS.rmdir(n):t.FS.unlink(n)}catch{o=!0}for(const[n,p]of D)c.get(n)!==p&&(o=!0);return o}function R(){t.setStdout(b),t.setStderr(b),L(),h(),L()}return R(),{run(o,c,w,n){var q,C,U;const p=[{text:"",dec:new TextDecoder},{text:"",dec:new TextDecoder}];let P=!1;const T=u=>d=>{const y=p[u];if(y.text.length<w){const I=y.dec.decode(d,{stream:!0}),K=w-y.text.length;I.length>K&&(P=!0),y.text+=I.slice(0,K)}else P=!0;return d.length};t.setStdout({write:T(0)}),t.setStderr({write:T(1)});const $=new TextEncoder().encode(c.replace(/\r\n?/g,`
`));let E=0;t.setStdin({read(u){const d=Math.min(u.length,$.length-E);return u.set($.subarray(E,E+d)),E+=d,d}});const v=r();let S=null,M=1,k=!1,B=performance.now(),W=!1;try{s(Y,v),S=v.get("__fairplay_run"),(q=n==null?void 0:n.onStart)==null||q.call(n),W=!0,B=performance.now(),M=Number(S(o))}catch(u){k=!0;const d=tt(u);if(d===0)M=0;else{const y=p[1];y.text&&!y.text.endsWith(`
`)&&(y.text+=`
`),y.text+=(d!==null?`Program exited with code ${d}.`:u instanceof Error?u.message:String(u))+`
`}}const V=performance.now()-B;W||(C=n==null?void 0:n.onStart)==null||C.call(n),(U=n==null?void 0:n.onEnd)==null||U.call(n);try{S==null||S.destroy(),v.destroy()}catch{}let x=k;if(!x)try{x=H()}catch{x=!0}if(x)try{t.setStdout(b),t.setStderr(b)}catch{}else try{R()}catch{x=!0}return{stdout:p[0].text,stderr:p[1].text,failed:M!==0,truncated:P,timeMs:V,restart:x}}}}const st=["fetch","XMLHttpRequest","WebSocket","WebSocketStream","WebTransport","EventSource","indexedDB","caches","importScripts","Worker","SharedWorker","BroadcastChannel","Request","Notification","requestFileSystem","webkitRequestFileSystem","requestFileSystemSync","webkitRequestFileSystemSync","cookieStore"];function nt(t){const e=st;for(const r of e){let i=t;for(;i;){try{Object.prototype.hasOwnProperty.call(i,r)&&delete i[r]}catch{}i=Object.getPrototypeOf(i)}try{Object.defineProperty(t,r,{value:void 0,writable:!1,configurable:!1})}catch{}}const s=t.navigator;if(s)for(const r of["storage","locks","serviceWorker","sendBeacon"]){let i=s;for(;i;){try{Object.prototype.hasOwnProperty.call(i,r)&&delete i[r]}catch{}i=Object.getPrototypeOf(i)}}}const m=self;let F=null,N=null;async function rt(t){const e=await import(`${t}pyodide.mjs`),s=console.warn;let r=()=>{};const i=new Promise((f,h)=>{console.warn=(...a)=>{s.apply(console,a),typeof a[0]=="string"&&a[0].includes("wasm instantiation failed")&&h(new Error(a[0]))},r=a=>{const g=a.reason??a.message;h(g instanceof Error?g:new Error(String(g)))},self.addEventListener("unhandledrejection",r)});let l;try{l=await Promise.race([e.loadPyodide({indexURL:t,fullStdLib:!1}),i])}finally{console.warn=s,self.removeEventListener("unhandledrejection",r)}const _=et(l);return nt(self),_}m.onmessage=async t=>{const e=t.data;if(e.type==="load"){try{N??(N=rt(e.base)),F=await N,m.postMessage({type:"ready"})}catch(s){N=null,m.postMessage({type:"loadError",message:s instanceof Error?s.message:String(s)})}return}if(e.type==="run"){if(!F){m.postMessage({type:"done",id:e.id,stdout:"",stderr:"Python is not loaded.",failed:!0,timeMs:0});return}try{const s=e.id,r=F.run(e.code,e.stdin,e.maxOutput,{onStart:()=>m.postMessage({type:"started",id:s}),onEnd:()=>m.postMessage({type:"ended",id:s})});m.postMessage({type:"done",id:e.id,...r})}catch(s){const r=(s instanceof Error?s.message:String(s))+`
`;m.postMessage({type:"done",id:e.id,stdout:"",stderr:r,failed:!0,timeMs:0,restart:!0})}}};
