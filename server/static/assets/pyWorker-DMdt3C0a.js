const G=["js","pyodide_js","pyodide","_pyodide","_pyodide_core","micropip"],J=`
import sys, builtins, gc, io, traceback, types
_blocked = ${JSON.stringify(G)}
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
`,z=`
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
`,A=new Set(["/dev","/proc","/sys"]);function k(t){const e=[];for(let s=0;s<t.length;s++){const o=t.get(s);e.push([o.get(0),o.get(1)]),o.destroy()}return e}function B(t){const e=new Map,s=o=>{let r;try{r=t.FS.readdir(o)}catch{return}for(const y of r){if(y==="."||y==="..")continue;const l=o==="/"?`/${y}`:`${o}/${y}`;if(A.has(l))continue;let a;try{a=t.FS.stat(l)}catch{continue}t.FS.isDir(a.mode)?(e.set(l,"dir"),s(l)):e.set(l,`${Number(a.mtime instanceof Date?a.mtime.getTime():a.mtime)}:${a.size}`)}};return s("/"),e}function H(t){const e=t.runPython(J,{globals:t.toPy({})}),[s,o,r,y,l,a,$]=[0,1,2,3,4,5,6].map(n=>e.get(n)),v=e.get(7),O=e.get(8),P=e.get(9),q=k(v),C=k(O),W=k(P);v.destroy(),O.destroy(),P.destroy(),e.destroy();const M=B(t),m={write:n=>n.length};function F(){for(const[n,c]of q)r(n),y(n,c);for(const[n,c]of C)l(n),a(n,c);for(const[n,c]of W)n(c)}function U(){let n=!1;const c=B(t),h=[...c.keys()].filter(i=>!M.has(i)).sort((i,_)=>_.length-i.length);for(const i of h)try{c.get(i)==="dir"?t.FS.rmdir(i):t.FS.unlink(i)}catch{n=!0}for(const[i,_]of M)c.get(i)!==_&&(n=!0);return n}function N(){t.setStdout(m),t.setStderr(m),F(),$(),F()}return N(),{run(n,c,h){N();const i=[{text:"",dec:new TextDecoder},{text:"",dec:new TextDecoder}];let _=!1;const D=d=>u=>{const x=i[d];if(x.text.length<h){const L=x.dec.decode(u,{stream:!0}),T=h-x.text.length;L.length>T&&(_=!0),x.text+=L.slice(0,T)}else _=!0;return u.length};t.setStdout({write:D(0)}),t.setStderr({write:D(1)});const R=new TextEncoder().encode(c);let g=0;t.setStdin({read(d){const u=Math.min(d.length,R.length-g);return d.set(R.subarray(g,g+u)),g+=u,u}});const S=o();let p=null,j=1;const I=performance.now();try{s(z,S),p=S.get("__fairplay_run"),j=Number(p(n))}catch(d){i[1].text+=(d instanceof Error?d.message:String(d))+`
`}const K=performance.now()-I;try{p==null||p.destroy(),S.destroy()}catch{}t.setStdout(m),t.setStderr(m);let b=!1;try{b=U()}catch{b=!0}return{stdout:i[0].text,stderr:i[1].text,failed:j!==0,truncated:_,timeMs:K,restart:b}}}}const V=["fetch","XMLHttpRequest","WebSocket","WebSocketStream","WebTransport","EventSource","indexedDB","caches","importScripts","Worker","SharedWorker","BroadcastChannel","Request","Notification","requestFileSystem","webkitRequestFileSystem","requestFileSystemSync","webkitRequestFileSystemSync","cookieStore"];function X(t){const e=V;for(const o of e){let r=t;for(;r;){try{Object.prototype.hasOwnProperty.call(r,o)&&delete r[o]}catch{}r=Object.getPrototypeOf(r)}try{Object.defineProperty(t,o,{value:void 0,writable:!1,configurable:!1})}catch{}}const s=t.navigator;if(s)for(const o of["storage","locks","serviceWorker","sendBeacon"]){let r=s;for(;r;){try{Object.prototype.hasOwnProperty.call(r,o)&&delete r[o]}catch{}r=Object.getPrototypeOf(r)}}}const f=self;let w=null,E=null;async function Q(t){const s=await(await import(`${t}pyodide.mjs`)).loadPyodide({indexURL:t,fullStdLib:!1}),o=H(s);return X(self),o}f.onmessage=async t=>{const e=t.data;if(e.type==="load"){try{E??(E=Q(e.base)),w=await E,f.postMessage({type:"ready"})}catch(s){E=null,f.postMessage({type:"loadError",message:s instanceof Error?s.message:String(s)})}return}if(e.type==="run"){if(!w){f.postMessage({type:"done",id:e.id,stdout:"",stderr:"Python is not loaded.",failed:!0,timeMs:0});return}try{const s=w.run(e.code,e.stdin,e.maxOutput);f.postMessage({type:"done",id:e.id,...s})}catch(s){const o=(s instanceof Error?s.message:String(s))+`
`;f.postMessage({type:"done",id:e.id,stdout:"",stderr:o,failed:!0,timeMs:0,restart:!0})}}};
