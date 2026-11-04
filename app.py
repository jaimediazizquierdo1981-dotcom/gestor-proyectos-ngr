"""Gestor de Proyectos NGR: API y vistas."""
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
import time as _time
from fastapi.staticfiles import StaticFiles

from motor import db, seguridad as seg
from motor.modelo import cargar_base, ejecutar

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Gestor de Proyectos NGR")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.iniciar()


@app.middleware("http")
async def sin_cache(request: Request, call_next):
    ruta = request.url.path
    if ruta not in seg.LIBRES and not seg.sesion_valida(request.cookies.get("sesion")):
        if ruta.startswith("/api/"):
            return JSONResponse({"detail": "Sesión vencida. Vuelve a ingresar."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# ---------- login
@app.get("/login", include_in_schema=False)
def vista_login():
    return FileResponse(STATIC / "login.html")


@app.get("/api/auth/captcha")
def api_captcha():
    svg, cookie, _ = seg.captcha()
    r = Response(svg, media_type="image/svg+xml")
    r.set_cookie("captcha", cookie, max_age=300, httponly=True, samesite="lax", secure=seg.SEGURA)
    r.headers["Cache-Control"] = "no-store"
    return r


@app.post("/api/auth/login")
def api_login(request: Request, body: dict = Body(...)):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if seg.bloqueado(ip):
        return JSONResponse({"detail": "Demasiados intentos. Espera 5 minutos."}, status_code=429)
    if not seg.captcha_ok(request.cookies.get("captcha", ""), str(body.get("captcha", ""))):
        seg.fallo(ip)
        return JSONResponse({"detail": "El código de verificación no coincide."}, status_code=400)
    if not seg.credenciales_ok(str(body.get("usuario", "")).strip(), str(body.get("password", ""))):
        seg.fallo(ip); _time.sleep(1)
        return JSONResponse({"detail": "Usuario o contraseña incorrectos."}, status_code=401)
    cookie, dur = seg.nueva_sesion()
    r = JSONResponse({"ok": True})
    r.set_cookie("sesion", cookie, max_age=dur, httponly=True, samesite="lax", secure=seg.SEGURA)
    r.delete_cookie("captcha")
    return r


@app.get("/logout", include_in_schema=False)
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie("sesion")
    return r


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


@app.get("/api/escenario")
def api_escenario():
    return db.leer_escenario() if db.disponible() else {"data": {}, "guardado": None}


@app.post("/api/hoja/guardar")
def api_guardar_hoja(body: dict = Body(...)):
    """Guarda en la base toda la hoja: asignaciones, posiciones, observaciones, opex y escenario (incluir, on hold, prioridad, inicio)."""
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    return {"ok": True, "guardado": db.guardar_hoja(body)}


@app.get("/api/plantilla/colaboradores")
def api_plantilla_colaboradores():
    from motor import plantillas
    from motor.modelo import PERFILES
    data = plantillas.plantilla(cargar_base()["equipo"], PERFILES)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_colaboradores.xlsx"'})


@app.post("/api/cargar/colaboradores")
async def api_cargar_colaboradores(archivo: UploadFile = File(...), modo: str = "actualizar", confirmar: bool = False):
    from motor import plantillas
    from motor.modelo import PERFILES
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    try:
        filas, errores = plantillas.leer(await archivo.read(), PERFILES)
    except Exception as e:  # archivo ilegible
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")
    resp = {"filas_validas": len(filas), "errores": errores, "muestra": filas[:8], "guardado": False}
    if confirmar:
        if not filas:
            raise HTTPException(400, "No hay filas válidas para cargar.")
        resp.update(db.cargar_colaboradores(filas, "reemplazar" if modo == "reemplazar" else "actualizar"), guardado=True)
    return resp


@app.delete("/api/hoja/ficha")
def api_eliminar_ficha(codigo: str):
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    db.eliminar_ficha(codigo)
    return {"ok": True}


@app.get("/api/estado")
def api_estado():
    return db.estado()


VISTAS = {"/": "index.html", "/criterios": "criterios.html", "/simulador": "simulador.html", "/colaboradores": "colaboradores.html", "/hoja": "hoja.html"}
for ruta, archivo in VISTAS.items():
    app.add_api_route(ruta, (lambda a=archivo: FileResponse(STATIC / a)), methods=["GET"], include_in_schema=False)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico")
