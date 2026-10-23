"""FastAPI application entry point for smart-androidbot."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import litellm

from db.database import init_db
from db import regression_models
from core.settings_store import ensure_seeded
from routers import appdist, automation, devices, knowledge, live, projects, recorder, serverinfo, settings, testsuites, testruns
from routers import testaccounts
from routers import auth as auth_router
from routers import account as account_router
from auth.middleware import AuthMiddleware
from ws.portal_ws import portal_websocket_endpoint, browser_usb_websocket_endpoint, adb_bridge_websocket_endpoint, hdc_bridge_websocket_endpoint, ios_bridge_websocket_endpoint
from ws.video import provider_video_endpoint
from routers import regressions

# Drop provider-unsupported params (e.g. vector_store_ids leaking into Anthropic)
litellm.drop_params = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Serve the React entry point when a client-side route has no file."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise
        return await super().get_response("index.html", scope)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised")
    await ensure_seeded()
    from core.device_scheduler import recover_interrupted_runs, shutdown
    recovered = await recover_interrupted_runs()
    if recovered:
        logger.info("Marked %d interrupted runs", recovered)
    try:
        yield
    finally:
        await shutdown()
        from routers.ios_local import shutdown as shutdown_ios
        await shutdown_ios()


app = FastAPI(title="smart-ai-bot", version="1.1.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware (JWT / API Key resolution → request.state)
app.add_middleware(AuthMiddleware)

# REST routers
app.include_router(auth_router.router)
app.include_router(testaccounts.router)
app.include_router(account_router.router)
from routers import ios_local
app.include_router(ios_local.router)
from routers import ios_hosts
app.include_router(ios_hosts.router)
app.include_router(devices.router)
app.include_router(testsuites.router)
app.include_router(testsuites.nodes_router)
app.include_router(testruns.router)
app.include_router(recorder.router)
app.include_router(settings.router)
app.include_router(appdist.router)
app.include_router(serverinfo.router)
app.include_router(live.router)
app.include_router(projects.router)
app.include_router(knowledge.router)
app.include_router(automation.router)
app.include_router(regressions.router)
app.include_router(regressions.download_router)

# Portal reverse WebSocket
app.add_api_websocket_route("/v1/providers/join", portal_websocket_endpoint)
app.add_api_websocket_route("/v1/providers/browser-usb", browser_usb_websocket_endpoint)
app.add_api_websocket_route("/v1/providers/adb-bridge", adb_bridge_websocket_endpoint)
app.add_api_websocket_route("/v1/providers/hdc-bridge", hdc_bridge_websocket_endpoint)
app.add_api_websocket_route("/v1/providers/ios-bridge", ios_bridge_websocket_endpoint)
app.add_api_websocket_route("/v1/providers/ios-host", ios_hosts.host_websocket)
app.add_api_websocket_route("/v1/providers/video", provider_video_endpoint)

# Live H.264 screen stream (ADB screenrecord) — under /v1 so the WS proxy applies
app.add_api_websocket_route("/v1/devices/{device_id}/live", live.stream_h264)
app.add_api_websocket_route("/v1/devices/{device_id}/scrcpy", live.stream_scrcpy)

# Serve built frontend (production)
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")
else:
    @app.get("/")
    async def root():
        return {"status": "ok", "frontend": "not built — run: cd frontend && npm run build"}
