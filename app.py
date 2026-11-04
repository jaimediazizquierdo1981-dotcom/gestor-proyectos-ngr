"""Gestor de Proyectos NGR: API y vistas."""
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from motor import db
from motor.modelo import cargar_base, ejecutar

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Gestor de Proyectos NGR")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.iniciar()


@app.middleware("http")
async def sin_cache(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.get("/api/base")
def api_base():
    return cargar_base()


@app.post("/api/calcular")
def api_calcular(entrada: dict = Body(default={})):
    """Cálculo rápido (sin Monte Carlo)."""
    return ejecutar(entrada, con_mc=False)


@app.post("/api/montecarlo")
def api_montecarlo(entrada: dict = Body(default={})):
    """Simulación PERT + Monte Carlo (más lenta)."""
    return ejecutar(entrada, con_mc=True)["montecarlo"]


@app.get("/api/estado")
def api_estado():
    return db.estado()


VISTAS = {"/": "index.html", "/criterios": "criterios.html", "/modelos": "modelos.html", "/simulador": "simulador.html"}
for ruta, archivo in VISTAS.items():
    app.add_api_route(ruta, (lambda a=archivo: FileResponse(STATIC / a)), methods=["GET"], include_in_schema=False)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico")
