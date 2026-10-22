const o=self;let r=null,u=null;const b=`
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
`;async function w(c){const t=await(await import(`${c}pyodide.mjs`)).loadPyodide({indexURL:c,fullStdLib:!1});return t.runPython(b),t}o.onmessage=async c=>{const e=c.data;if(e.type==="load"){try{u??(u=w(e.base)),r=await u,o.postMessage({type:"ready"})}catch(t){u=null,o.postMessage({type:"loadError",message:t instanceof Error?t.message:String(t)})}return}if(e.type==="run"){if(!r){o.postMessage({type:"done",id:e.id,stdout:"",stderr:"Python is not loaded.",failed:!0,timeMs:0});return}const t=e.maxOutput,i=[{text:"",dec:new TextDecoder},{text:"",dec:new TextDecoder}];let l=!1;const p=n=>s=>{const d=i[n];if(d.text.length<t){const m=d.dec.decode(s,{stream:!0}),_=t-d.text.length;m.length>_&&(l=!0),d.text+=m.slice(0,_)}else l=!0;return s.length};r.setStdout({write:p(0)}),r.setStderr({write:p(1)});const y=new TextEncoder().encode(e.stdin);let a=0;r.setStdin({read(n){const s=Math.min(n.length,y.length-a);return n.set(y.subarray(a,a+s)),a+=s,s}});const f=r.globals.get("__fairplay_run"),g=performance.now();let x=1;try{x=f(e.code)}catch(n){i[1].text+=(n instanceof Error?n.message:String(n))+`
`}const h=performance.now()-g;o.postMessage({type:"done",id:e.id,stdout:i[0].text,stderr:i[1].text,failed:x!==0,truncated:l,timeMs:h})}};
