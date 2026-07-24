import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app.db import engine
from app.routes import items, alerts, labels, auth, admin, config, imports, orders, pallets, reports, aspel_sync, analytics, monitor
from app.scheduler import auto_sync_loop
from app.services.mqtt_monitor import start_mqtt_monitor, stop_mqtt_monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_task = asyncio.create_task(auto_sync_loop())
    mqtt_client = start_mqtt_monitor()  # paho thread (Windows-safe); None if disabled
    yield
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass
    stop_mqtt_monitor(mqtt_client)


app = FastAPI(title="Biotecnica Production", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(items.router, prefix="/items", tags=["items"])
app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
app.include_router(labels.router, prefix="/labels", tags=["labels"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(config.router, prefix="/config", tags=["config"])
app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(orders.router, prefix="/orders", tags=["orders"])
app.include_router(pallets.router, prefix="/pallets", tags=["pallets"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(aspel_sync.router, prefix="/aspel", tags=["aspel"])
app.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
app.include_router(monitor.router, prefix="/monitor", tags=["monitor"])

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.get("/")
def root():
    return {"message": "Backend corriendo"}


@app.get("/db-test")
def db_test():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1 as test;"))
        row = result.fetchone()
        return {"db_ok": True, "result": row[0]}


@app.get("/ui")
def ui(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/index")
def index_alias(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/operator")
def operator_ui(request: Request, station: str = None):
    mode = "almacen" if station == "almacen" else "produccion"
    return templates.TemplateResponse(request=request, name="operator.html", context={"mode": mode})

@app.get("/almacen")
def almacen_ui(request: Request):
    return templates.TemplateResponse(request=request, name="operator.html", context={"mode": "almacen"})