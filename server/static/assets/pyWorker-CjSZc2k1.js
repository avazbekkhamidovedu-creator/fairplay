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
`,Z=new Set(["/dev","/proc","/sys"]);function P(t){const e=[];for(let s=0;s<t.length;s++){const r=t.get(s);e.push([r.get(0),r.get(1)]),r.destroy()}return e}function K(t){const e=new Map,s=r=>{let o;try{o=t.FS.readdir(r)}catch{return}for(const p of o){if(p==="."||p==="..")continue;const f=r==="/"?`/${p}`:`${r}/${p}`;if(Z.has(f))continue;let _;try{_=t.FS.stat(f)}catch{continue}t.FS.isDir(_.mode)?(e.set(f,"dir"),s(f)):e.set(f,`${Number(_.mtime instanceof Date?_.mtime.getTime():_.mtime)}:${_.size}`)}};return s("/"),e}function tt(t){var s;if(!t||typeof t!="object")return null;const e=t;return typeof e.status=="number"&&(e.name==="Exit"||((s=e.constructor)==null?void 0:s.name)==="Exit")?e.status:null}function et(t){const e=t.runPython(Q,{globals:t.toPy({})}),[s,r,o,p,f,_,G]=[0,1,2,3,4,5,6].map(i=>e.get(i)),O=e.get(7),F=e.get(8),N=e.get(9),J=P(O),z=P(F),A=P(N);O.destroy(),F.destroy(),N.destroy(),e.destroy();const D=K(t),x={write:i=>i.length};function j(){for(const[i,c]of J)o(i),p(i,c);for(const[i,c]of z)f(i),_(i,c);for(const[i,c]of A)i(c)}function H(){let i=!1;const c=K(t),S=[...c.keys()].filter(n=>!D.has(n)).sort((n,u)=>u.length-n.length);for(const n of S)try{c.get(n)==="dir"?t.FS.rmdir(n):t.FS.unlink(n)}catch{i=!0}for(const[n,u]of D)c.get(n)!==u&&(i=!0);return i}function R(){t.setStdout(x),t.setStderr(x),j(),G(),j()}return R(),{run(i,c,S,n){var W,q,C;const u=[{text:"",dec:new TextDecoder},{text:"",dec:new TextDecoder}];let b=!1;const L=d=>a=>{const l=u[d];if(l.text.length<S){const U=l.dec.decode(a,{stream:!0}),I=S-l.text.length;U.length>I&&(b=!0),l.text+=U.slice(0,I)}else b=!0;return a.length};t.setStdout({write:L(0)}),t.setStderr({write:L(1)});const T=new TextEncoder().encode(c.replace(/\r\n?/g,`
`));let h=0;t.setStdin({read(d){const a=Math.min(d.length,T.length-h);return d.set(T.subarray(h,h+a)),h+=a,a}});const w=r();let g=null,E=1,$=!1,k=performance.now(),B=!1;try{s(Y,w),g=w.get("__fairplay_run"),(W=n==null?void 0:n.onStart)==null||W.call(n),B=!0,k=performance.now(),E=Number(g(i))}catch(d){$=!0;const a=tt(d);if(a===0)E=0;else{const l=u[1];l.text&&!l.text.endsWith(`
`)&&(l.text+=`
`),l.text+=(a!==null?`Program exited with code ${a}.`:d instanceof Error?d.message:String(d))+`
`}}const V=performance.now()-k;B||(q=n==null?void 0:n.onStart)==null||q.call(n),(C=n==null?void 0:n.onEnd)==null||C.call(n);try{g==null||g.destroy(),w.destroy()}catch{}let m=$;if(!m)try{m=H()}catch{m=!0}if(m)try{t.setStdout(x),t.setStderr(x)}catch{}else try{R()}catch{m=!0}return{stdout:u[0].text,stderr:u[1].text,failed:E!==0,truncated:b,timeMs:V,restart:m}}}}const st=["fetch","XMLHttpRequest","WebSocket","WebSocketStream","WebTransport","EventSource","indexedDB","caches","importScripts","Worker","SharedWorker","BroadcastChannel","Request","Notification","requestFileSystem","webkitRequestFileSystem","requestFileSystemSync","webkitRequestFileSystemSync","cookieStore"];function nt(t){const e=st;for(const r of e){let o=t;for(;o;){try{Object.prototype.hasOwnProperty.call(o,r)&&delete o[r]}catch{}o=Object.getPrototypeOf(o)}try{Object.defineProperty(t,r,{value:void 0,writable:!1,configurable:!1})}catch{}}const s=t.navigator;if(s)for(const r of["storage","locks","serviceWorker","sendBeacon"]){let o=s;for(;o;){try{Object.prototype.hasOwnProperty.call(o,r)&&delete o[r]}catch{}o=Object.getPrototypeOf(o)}}}const y=self;let v=null,M=null;async function rt(t){const s=await(await import(`${t}pyodide.mjs`)).loadPyodide({indexURL:t,fullStdLib:!1}),r=et(s);return nt(self),r}y.onmessage=async t=>{const e=t.data;if(e.type==="load"){try{M??(M=rt(e.base)),v=await M,y.postMessage({type:"ready"})}catch(s){M=null,y.postMessage({type:"loadError",message:s instanceof Error?s.message:String(s)})}return}if(e.type==="run"){if(!v){y.postMessage({type:"done",id:e.id,stdout:"",stderr:"Python is not loaded.",failed:!0,timeMs:0});return}try{const s=e.id,r=v.run(e.code,e.stdin,e.maxOutput,{onStart:()=>y.postMessage({type:"started",id:s}),onEnd:()=>y.postMessage({type:"ended",id:s})});y.postMessage({type:"done",id:e.id,...r})}catch(s){const r=(s instanceof Error?s.message:String(s))+`
`;y.postMessage({type:"done",id:e.id,stdout:"",stderr:r,failed:!0,timeMs:0,restart:!0})}}};
