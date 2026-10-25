"""A small WebKitGTK host with a real navigation delegate, driven over JSON lines on stdin/stdout.

It plays the part of the Mac app's WKWebView: every navigation action (links, scripts, form posts and each
redirect hop) and each response is held until the controller answers allow or block, and commit,
finish and uri changes are reported. Runs under the system python3 (PyGObject + WebKit2 4.1).

Events out:  {"ev":"decide","id":n,"kind":"nav"|"resp","url":...,"main":bool,"redirect":bool,"type":str}
             {"ev":"commit","url":...}  {"ev":"finish","url":...}  {"ev":"uri","url":...}
             {"ev":"failed","url":...,"error":...}  {"ev":"newwindow","url":...}
Commands in: {"id":n,"cmd":"load","url":...} load/back/eval/info/quit, {"cmd":"decision","id":n,"allow":bool}
"""
import json
import os
import sys
import threading

os.environ.setdefault("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

out_lock = threading.Lock()


LOG = os.environ.get("WKHOST_LOG")


def log(kind, obj):
    if LOG:
        with open(LOG, "a") as f:
            f.write(kind + " " + json.dumps(obj)[:300] + "\n")


def emit(obj):
    log("out", obj)
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


dm = WebKit2.WebsiteDataManager.new_ephemeral()
try:
    dm.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)
except Exception:
    pass
ctx = WebKit2.WebContext.new_with_website_data_manager(dm)
try:
    ctx.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)
except Exception:
    pass
try:
    dm.get_cookie_manager().set_accept_policy(WebKit2.CookieAcceptPolicy.ALWAYS)
except Exception:
    pass
view = WebKit2.WebView.new_with_context(ctx)
view.get_settings().set_property("enable-developer-extras", False)
win = Gtk.Window()
win.set_default_size(1100, 800)
win.add(view)
win.show_all()

pending = {}
counter = [0]


def hold(decision, payload):
    counter[0] += 1
    n = counter[0]
    pending[n] = decision
    emit({"ev": "decide", "id": n, **payload})
    return True


def on_policy(v, decision, dtype):
    if dtype in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
        na = decision.get_navigation_action()
        req = na.get_request()
        try:
            redirect = bool(na.is_redirect())
        except Exception:
            redirect = False
        main = not decision.get_frame_name()
        if dtype == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            emit({"ev": "newwindow", "url": req.get_uri()})
            decision.ignore()
            return True
        return hold(decision, {"kind": "nav", "url": req.get_uri(), "main": main, "redirect": redirect,
                               "type": str(na.get_navigation_type().value_nick), "method": req.get_http_method()})
    if dtype == WebKit2.PolicyDecisionType.RESPONSE:
        resp = decision.get_response()
        headers = resp.get_http_headers()
        disp = headers.get_one("Content-Disposition") if headers else None
        return hold(decision, {"kind": "resp", "url": resp.get_uri(), "main": True, "redirect": False,
                               "type": "response", "supported": bool(decision.is_mime_type_supported()),
                               "disposition": disp})
    return False


shown = {"url": None, "provisional": False}


def on_load(v, event):
    """WKWebView.url is the committed page: a provisional load or redirect does not change it."""
    if event in (WebKit2.LoadEvent.STARTED, WebKit2.LoadEvent.REDIRECTED):
        shown["provisional"] = True
    elif event == WebKit2.LoadEvent.COMMITTED:
        shown["provisional"] = False
        shown["url"] = v.get_uri()
        emit({"ev": "commit", "url": shown["url"]})
        emit({"ev": "uri", "url": shown["url"]})
    elif event == WebKit2.LoadEvent.FINISHED:
        emit({"ev": "finish", "url": shown["url"]})


def on_uri(v, _):
    if not shown["provisional"]:
        shown["url"] = v.get_uri()
        emit({"ev": "uri", "url": shown["url"]})


def on_failed(v, event, uri, error):
    shown["provisional"] = False
    emit({"ev": "failed", "url": uri, "error": error.message})
    return False


def on_tls(v, uri, cert, errors):
    return False


view.connect("decide-policy", on_policy)
view.connect("load-changed", on_load)
view.connect("load-failed", on_failed)
view.connect("notify::uri", on_uri)
view.connect("create", lambda v, na: None)


def reply(cid, **kw):
    emit({"re": cid, **kw})


def run_js(cid, script):
    wrapped = "JSON.stringify({v: (function () { return (" + script + "); })()})"

    def done(v, res, data):
        try:
            val = v.evaluate_javascript_finish(res)
            text = val.to_string()
            reply(cid, ok=True, value=json.loads(text)["v"] if text else None)
        except Exception as e:  # noqa: BLE001
            reply(cid, ok=False, error=str(e))

    view.evaluate_javascript(wrapped, -1, None, None, None, done, None)


def handle(cmd):
    log("in", cmd)
    c, cid = cmd.get("cmd"), cmd.get("id")
    if c == "decision":
        d = pending.pop(cmd["id"], None)
        if d is not None:
            d.use() if cmd["allow"] else d.ignore()
    elif c == "load":
        view.load_uri(cmd["url"])
        reply(cid, ok=True)
    elif c == "back":
        view.go_back()
        reply(cid, ok=True)
    elif c == "eval":
        run_js(cid, cmd["script"])
    elif c == "info":
        item = view.get_back_forward_list().get_back_item()
        reply(cid, ok=True, url=shown["url"] or "", back=item.get_uri() if item else None,
              loading=bool(view.is_loading()), title=view.get_title())
    elif c == "quit":
        reply(cid, ok=True)
        Gtk.main_quit()
    return False


def reader():
    for line in sys.stdin:
        line = line.strip()
        if line:
            GLib.idle_add(handle, json.loads(line))
    GLib.idle_add(Gtk.main_quit)


threading.Thread(target=reader, daemon=True).start()
emit({"ev": "ready"})
Gtk.main()
