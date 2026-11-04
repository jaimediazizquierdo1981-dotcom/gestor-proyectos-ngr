"""Gestor de Proyectos NGR: API y vistas."""
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
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


@app.put("/api/colaborador")
def api_colaborador(body: dict = Body(...)):
    """Cambia el rol (perfil) o los días por semana de un colaborador."""
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    from motor.modelo import PERFILES
    rol = body.get("perfil")
    if rol is not None and rol not in PERFILES:
        raise HTTPException(400, "Rol no válido.")
    hs = body.get("dias_semana")
    if not db.actualizar_colaborador(str(body.get("persona", "")), rol, float(hs) if hs is not None else None):
        raise HTTPException(404, "Colaborador no encontrado.")
    return {"ok": True}


@app.get("/api/asignaciones")
def api_asignaciones():
    return db.leer_asignaciones() if db.disponible() else []


@app.put("/api/asignaciones")
def api_guardar_asignacion(body: dict = Body(...)):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    db.guardar_asignacion(str(body["codigo"]), str(body["persona"]), body.get("dias") or {})
    return {"ok": True}


@app.delete("/api/asignaciones")
def api_borrar_asignacion(codigo: str, persona: str):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    db.borrar_asignacion(codigo, persona)
    return {"ok": True}


@app.get("/api/necesidades")
def api_necesidades():
    return db.leer_necesidades() if db.disponible() else {}


@app.put("/api/necesidades")
def api_guardar_necesidades(body: dict = Body(...)):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    db.guardar_necesidades(str(body["codigo"]), body.get("items") or [])
    return {"ok": True}


@app.put("/api/proyecto")
def api_proyecto(body: dict = Body(...)):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    op = body.get("opex_mensual")
    db.actualizar_proyecto(str(body["codigo"]), float(op) if op is not None else None)
    return {"ok": True}


@app.get("/api/observaciones")
def api_observaciones():
    return db.leer_observaciones() if db.disponible() else {}


@app.put("/api/observaciones")
def api_guardar_observacion(body: dict = Body(...)):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    campo = str(body.get("campo"))
    if campo not in ("capex", "opex"):
        raise HTTPException(400, "Campo no válido.")
    db.guardar_observacion(str(body["codigo"]), campo, str(body.get("texto", ""))[:4000])
    return {"ok": True}


@app.get("/api/estado")
def api_estado():
    return db.estado()


VISTAS = {"/": "index.html", "/criterios": "criterios.html", "/modelos": "modelos.html", "/simulador": "simulador.html", "/colaboradores": "colaboradores.html", "/hoja": "hoja.html"}
for ruta, archivo in VISTAS.items():
    app.add_api_route(ruta, (lambda a=archivo: FileResponse(STATIC / a)), methods=["GET"], include_in_schema=False)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico")
