"""Proctora API server. Run from server/: `uvicorn app.main:app --port 8000`."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from .http_guard import GuardMiddleware, SignPrivateUploads
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from .admission import AdmissionLimit
from .app_version import UpgradeRequired, upgrade_required_handler
from .config import Settings
from .db import Database, set_db
from . import monitoring
from .routes_auth import router as auth_router
from .routes_gradebook import gradebook_router, grading_router
from .routes_insights import router as insights_router  # slice 6 F/G
from .routes_lms import router as lms_router
from .routes_practice import router as practice_router, student_router as practice_student_router
from .routes_sealed import router as sealed_router
from .routes_student import router as student_router
from .routes_teacher import router as teacher_router
from .trial import refresh_trial_cache, router as trial_router
from .seed import refuse_public_demo_logins, seed, seed_demo_class
from .routes_classes import student_router as classes_student_router, teacher_router as classes_teacher_router
from .static_files import StaticCache, is_asset_path, serve as serve_static, versioned_asset
from .store import make_store, set_store
from .timeutil import iso, utcnow

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("proctora")
# Security review 2026-10-24: httpx logs every request URL at INFO, and DKU OJ URLs carry
# NetIDs (GET .../students/<netid>/classes). Only warnings and errors from the HTTP clients.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    monitoring.init_sentry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.check_secure()
        db = Database(settings)
        await db.create_all()
        await seed(db, settings)
        if settings.dev:
            await seed_demo_class(db)
        try:
            await refuse_public_demo_logins(db, settings)
        except RuntimeError:
            await db.dispose()
            raise
        store = make_store(settings.redis_url)
        set_db(db)
        set_store(store)
        await refresh_trial_cache()        # the seed may have changed the sample exam
        # Audit L-06: resume scan batches a restart or crash left unfinished.
        sweeper = None
        if settings.feature("scan"):
            from .routes_scan import sweep_forever
            sweeper = asyncio.get_running_loop().create_task(sweep_forever())
        # integrations (app.integrations): their background loops while connected
        from .integrations import registry as integrations
        bridges = [asyncio.get_running_loop().create_task(task())
                   for task in integrations.background_tasks(settings)]
        # 2026-10-23: exams with a close time close by themselves (app.closing)
        from . import closing
        if closing.BACKGROUND:
            bridges.append(asyncio.get_running_loop().create_task(closing.run_forever()))
        try:
            yield
        finally:
            for bridge in bridges:
                bridge.cancel()
                try:
                    await bridge
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if sweeper is not None:
                sweeper.cancel()
                try:                       # wait for it: disposing the DB under it can hang
                    await sweeper
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await store.close()
            await db.dispose()
            set_db(None)
            set_store(None)

    # Audit 2026-10-13 (hardening): the API explorer only on a dev server.
    docs = settings.dev
    app = FastAPI(title="Proctora", version="0.1", lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url="/redoc" if docs else None,
                  openapi_url="/openapi.json" if docs else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo the rejected input back: it can be large, private (answers), or not
        # even JSON-encodable (NaN / Infinity), which used to turn a 422 into a 500.
        detail = [{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)
    app.state.settings = settings
    app.add_exception_handler(UpgradeRequired, upgrade_required_handler)   # S2: 426
    from .browser_gate import BrowserGate
    app.add_middleware(BrowserGate, mode=settings.browser_gate)   # innermost: answers carry the usual headers
    app.add_middleware(SignPrivateUploads)     # audit L-18: expiring links to scan images
    app.add_middleware(GuardMiddleware)        # audit L-04 / S-16: size limits + headers
    # Audit #12: no wildcard CORS. The UI is served by this server (same origin); other
    # origins only when listed in FAIRPLAY_CORS_ORIGINS (dev: the Vite dev server).
    origins = list(settings.cors_origins)
    if settings.dev and not origins:
        origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"],
                           allow_headers=["*"])
    # perf 0.6.2: bounded work in progress per worker; the rest queue (app/admission.py)
    app.add_middleware(AdmissionLimit, limit=settings.max_inflight,
                       priority_limit=min(max(1, settings.max_inflight // 4),
                                          max(4, (settings.db_pool_size + settings.db_max_overflow) // 2)))

    # perf 0.6.2: the student router first. Requests are matched against the routers in
    # this order, and the exam's per-student traffic (heartbeat, app beat, saves) is ~95%
    # of all requests during an exam. Its paths overlap no other router's
    # (tests/test_perf_0_6_2.py checks), so the order changes nothing else.
    app.include_router(student_router)
    app.include_router(auth_router)
    app.include_router(classes_teacher_router)   # audit S-03
    app.include_router(classes_student_router)
    app.include_router(teacher_router)
    app.include_router(trial_router)             # 2026-10-22: sample exam (app.trial)
    app.include_router(sealed_router)            # S5: sealed offline hand-in
    app.include_router(grading_router)
    app.include_router(gradebook_router)
    if settings.feature("lms"):
        app.include_router(lms_router)  # slice 5 (before the /api catch-all below)
    app.include_router(insights_router)  # slice 6 F/G: insights + student profiles
    if settings.feature("practice"):
        app.include_router(practice_router)  # slice 6 H: practice assignments (formative)
        app.include_router(practice_student_router)
    # slice 6 A/B/C: offline packages, uploads, topics
    from .offline import router as offline_router
    from .routes_topics import router as topics_router
    from .uploads import router as uploads_router, set_uploads_dir
    set_uploads_dir(settings.uploads_dir)
    app.include_router(offline_router)
    app.include_router(uploads_router)
    # 2026-10-23 (exam tools): open-book materials
    from .materials import student_router as materials_student_router, teacher_router as materials_teacher_router
    app.include_router(materials_student_router)
    app.include_router(materials_teacher_router)
    # 2026-10-23 (review): can a website be shown inside the exam (teacher's settings)
    from .site_check import router as site_check_router
    app.include_router(site_check_router)
    app.include_router(topics_router)
    # integrations: live-room links, status, statement assets; file_upload answer files
    from .answer_files import router as answer_files_router
    from .integrations import registry as integration_registry
    from .integrations.routes import router as integrations_router
    integration_registry.configure(settings)
    app.include_router(answer_files_router)
    app.include_router(integrations_router)
    for r in integration_registry.routers():
        app.include_router(r)
    if settings.feature("scan"):
        from .routes_scan import router as scan_router  # slice 6 D: Scan & Grade (paper exams)
        app.include_router(scan_router)

    monitoring.install(app)

    @app.get("/api/health")
    async def health():
        # demo_logins: the login page may show the demo passwords (dev / demo servers only).
        # features: optional parts switched on (FAIRPLAY_FEATURES); the UI hides the rest.
        # browser_demo: only with FAIRPLAY_BROWSER_GATE=off; the web then stands in for the app's bridge
        out = {"ok": True, "demo_logins": settings.demo_logins, "features": list(settings.features)}
        if settings.browser_gate == "off":
            out["browser_demo"] = True
        return out

    @app.get("/api/app/version")
    async def app_version():
        # S2: no auth, so an app can tell "update first" before the student signs in.
        return {"mac": settings.min_app_version("mac"), "windows": settings.min_app_version("windows"),
                "ipad": settings.min_app_version("ipad"), "server_time": iso(utcnow())}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                   include_in_schema=False)
    async def api_not_found(rest: str):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    static_root = os.path.realpath(settings.static_dir)
    # perf 0.6.2: long-lived caching for hashed /assets/, 304s, gzip (app/static_files.py)
    static_cache = StaticCache()

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/teacher", status_code=307)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request):
        # 0.6.2 review: the pack is requested as /app-assets/v/<version>/<file>; immutable when
        # <version> is the pack on disk, so a release that upgrades Pyodide or pdf.js never
        # mixes a cached old file with new page code (static_files.versioned_asset).
        cache_override = None
        versioned = versioned_asset(path, static_root)
        if versioned is not None:
            path, cache_override = versioned
        candidate = os.path.realpath(os.path.join(static_root, path))
        if (candidate.startswith(static_root + os.sep) and os.path.isfile(candidate)):
            # 2026-10-23: .mjs (pdf.js worker) and .wasm (Pyodide) get their own types
            return await serve_static(request, candidate, os.path.relpath(candidate, static_root),
                                      static_cache, is_index=candidate == os.path.join(static_root, "index.html"),
                                      cache_override=cache_override)
        if is_asset_path(path):
            # a missing script or pack file is a 404, never the app's HTML page
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index = os.path.join(static_root, "index.html")
        if os.path.isfile(index):
            return await serve_static(request, index, "index.html", static_cache, is_index=True)
        return JSONResponse({"detail": "UI not built"}, status_code=404)

    return app


app = create_app()
