"""Gestor de Proyectos NGR: API y vistas."""
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
import time as _time
from fastapi.staticfiles import StaticFiles

from motor import auditoria, db, seguridad as seg
from motor.modelo import cargar_base, ejecutar

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Gestor de Proyectos NGR")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
db.iniciar()
seg.HAY_USUARIOS = db.hay_usuarios()
CAMBIO_LIBRE = {"/cambiar-clave", "/api/auth/cambiar", "/api/yo", "/logout", "/static/comun.css"}


def _quien(request: Request) -> dict | None:
    """Usuario de la sesión: de la tabla de usuarios o el de emergencia (variables de entorno, administrador)."""
    u = seg.usuario_sesion(request.cookies.get("sesion"))
    if not u:
        return None
    if seg.APP_USER and u == seg.APP_USER.lower():
        return {"usuario": u, "nombre": u, "area": "", "es_admin": True, "debe_cambiar": False, "activo": True, "emergencia": True}
    d = db.leer_usuario(u) if db.disponible() else None
    return d if d and d["activo"] else None


def _admin(request: Request) -> dict:
    q = _quien(request)
    if not q or not q["es_admin"]:
        raise HTTPException(403, "Solo el administrador puede gestionar usuarios.")
    return q


@app.middleware("http")
async def sin_cache(request: Request, call_next):
    ruta = request.url.path
    q = None
    if ruta not in seg.LIBRES and seg.activo():
        q = _quien(request)
        if not q:
            if ruta.startswith("/api/"):
                return JSONResponse({"detail": "Sesión vencida. Vuelve a ingresar."}, status_code=401)
            r = RedirectResponse("/login", status_code=303); r.delete_cookie("sesion"); return r
        if q["debe_cambiar"] and ruta not in CAMBIO_LIBRE and not ruta.startswith("/static/"):
            if ruta.startswith("/api/"):
                return JSONResponse({"detail": "Debes cambiar tu contraseña."}, status_code=403)
            return RedirectResponse("/cambiar-clave", status_code=303)
    accion = auditoria.accion(request.method, ruta)
    cuerpo = await request.body() if accion and "multipart" not in request.headers.get("content-type", "") else b""
    if accion == "Salida":
        q = q or _quien(request)
    resp = await call_next(request)
    if accion:
        try:
            auditoria.registrar(request, accion, q, cuerpo, resp.status_code)
        except Exception as e:   # el log nunca debe romper la operación
            print("AUDIT error:", e, flush=True)
    if q and accion != "Salida":   # sesión deslizante: cada solicitud renueva los 10 minutos
        cookie, dur = seg.nueva_sesion(q["usuario"])
        resp.set_cookie("sesion", cookie, max_age=dur, httponly=True, samesite="lax", secure=seg.SEGURA)
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
    usuario, clave = str(body.get("usuario", "")).strip().lower(), str(body.get("password", ""))
    u = db.leer_usuario(usuario, con_hash=True) if db.disponible() and usuario else None
    if u and u["activo"] and seg.verificar_clave(clave, u["clave_hash"]):
        db.marcar_ingreso(usuario)
        cambiar = u["debe_cambiar"]
    elif seg.credenciales_ok(usuario, clave):
        cambiar = False
    else:
        seg.fallo(ip); _time.sleep(1)
        return JSONResponse({"detail": "Usuario o contraseña incorrectos."}, status_code=401)
    cookie, dur = seg.nueva_sesion(usuario)
    r = JSONResponse({"ok": True, "cambiar": cambiar})
    r.set_cookie("sesion", cookie, max_age=dur, httponly=True, samesite="lax", secure=seg.SEGURA)
    r.delete_cookie("captcha")
    return r


@app.get("/cambiar-clave", include_in_schema=False)
def vista_cambiar():
    return FileResponse(STATIC / "cambiar.html")


@app.get("/api/yo")
def api_yo(request: Request):
    q = _quien(request)
    if not q:
        return {"usuario": "", "nombre": "", "es_admin": not seg.activo(), "debe_cambiar": False}
    return {k: q.get(k) for k in ("usuario", "nombre", "area", "es_admin", "debe_cambiar", "emergencia")}


@app.post("/api/auth/cambiar")
def api_cambiar(request: Request, body: dict = Body(...)):
    q = _quien(request)
    if not q or q.get("emergencia"):
        raise HTTPException(400, "Este usuario no puede cambiar su contraseña aquí.")
    actual, nueva = str(body.get("actual", "")), str(body.get("nueva", ""))
    u = db.leer_usuario(q["usuario"], con_hash=True)
    if not seg.verificar_clave(actual, u["clave_hash"]):
        _time.sleep(1)
        raise HTTPException(400, "La contraseña actual no es correcta.")
    if nueva == actual:
        raise HTTPException(400, "La nueva contraseña debe ser distinta de la actual.")
    err = seg.clave_valida(nueva)
    if err:
        raise HTTPException(400, err)
    db.guardar_clave(q["usuario"], seg.hash_clave(nueva), False)
    return {"ok": True}


# ---------- usuarios (solo administrador)
@app.get("/usuarios", include_in_schema=False)
def vista_usuarios(request: Request):
    q = _quien(request)
    if seg.activo() and (not q or not q["es_admin"]):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "usuarios.html")


@app.get("/api/usuarios")
def api_usuarios(request: Request):
    _admin(request)
    return db.listar_usuarios()


@app.post("/api/usuarios")
def api_crear_usuario(request: Request, body: dict = Body(...)):
    _admin(request)
    import re
    usuario = str(body.get("usuario", "")).strip().lower()
    nombre = str(body.get("nombre", "")).strip()
    if not re.fullmatch(r"[a-z][a-z0-9._-]{2,29}", usuario):
        raise HTTPException(400, "Usuario inválido: 3 a 30 caracteres, solo minúsculas, números, punto, guion o guion bajo.")
    if not nombre:
        raise HTTPException(400, "Falta el nombre.")
    if db.leer_usuario(usuario) or (seg.APP_USER and usuario == seg.APP_USER.lower()):
        raise HTTPException(400, f"El usuario {usuario} ya existe.")
    temporal = seg.clave_temporal()
    db.crear_usuario(usuario, nombre, str(body.get("area", "")).strip(), seg.hash_clave(temporal), bool(body.get("es_admin")))
    seg.HAY_USUARIOS = True
    return {"ok": True, "usuario": usuario, "temporal": temporal}


@app.post("/api/usuarios/{usuario}/reset")
def api_reset_usuario(request: Request, usuario: str):
    _admin(request)
    if not db.leer_usuario(usuario):
        raise HTTPException(404, "No existe el usuario.")
    temporal = seg.clave_temporal()
    db.guardar_clave(usuario, seg.hash_clave(temporal), True)
    return {"ok": True, "usuario": usuario, "temporal": temporal}


@app.put("/api/usuarios/{usuario}")
def api_editar_usuario(request: Request, usuario: str, body: dict = Body(...)):
    q = _admin(request)
    if not db.leer_usuario(usuario):
        raise HTTPException(404, "No existe el usuario.")
    if usuario.lower() == q["usuario"] and (body.get("activo") is False or body.get("es_admin") is False):
        raise HTTPException(400, "No puedes desactivarte ni quitarte el rol de administrador a ti mismo.")
    db.actualizar_usuario(usuario, {k: body[k] for k in ("nombre", "area", "es_admin", "activo") if k in body})
    return {"ok": True}


# ---------- actividad (log de auditoría, solo administrador)
@app.get("/actividad", include_in_schema=False)
def vista_actividad(request: Request):
    q = _quien(request)
    if seg.activo() and (not q or not q["es_admin"]):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "actividad.html")


@app.get("/api/logs")
def api_logs(request: Request, usuario: str = "", desde: str = "", hasta: str = "", texto: str = "", limite: int = 500):
    _admin(request)
    return db.leer_logs(usuario, desde, hasta, texto, min(max(limite, 1), 5000))


@app.get("/logout", include_in_schema=False)
def logout(motivo: str = ""):
    r = RedirectResponse("/login?motivo=inactividad" if motivo == "inactividad" else "/login", status_code=303)
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
    if body.get("capex") is not None:
        db.actualizar_capex(str(body["codigo"]), max(0.0, float(body["capex"] or 0)))
    om = body.get("opex_meses", False)
    db.actualizar_proyecto(str(body["codigo"]), float(op) if op is not None else None,
                           om if om is False else (None if om in (None, "") else float(om)))
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
    if not isinstance(body.get("codigos"), list):
        raise HTTPException(409, "Esta pestaña tiene una versión antigua de la Hoja de trabajo. Recarga la página (F5) y vuelve a guardar; tus cambios ya hechos se guardaron al editarlos.")
    if not body["codigos"]:
        return {"ok": True, "guardado": None, "fichas": 0}
    return {"ok": True, "guardado": db.guardar_hoja(body), "fichas": len(body["codigos"])}


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


def _activos():
    o = ejecutar({}, con_mc=False)
    return o, [p for p in sorted(o["proyectos"], key=lambda x: x["prioridad"] or 999) if p["incluir"] and not p.get("on_hold")]


def _roles_cargados(o) -> list[str]:
    """Solo los roles que tienen colaboradores cargados (propios o terceros)."""
    from motor.modelo import PERFILES
    hay = {x["perfil"] for x in o.get("personas", [])}
    return [r for r in PERFILES if r in hay]


@app.get("/api/plantilla/posiciones")
def api_plantilla_posiciones():
    from motor import plantillas
    from motor.modelo import PERFILES
    o, act = _activos()
    data = plantillas.plantilla_posiciones(act, db.leer_necesidades() if db.disponible() else {}, o["meses"], _roles_cargados(o))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_posiciones_por_proyecto.xlsx"'})


@app.post("/api/cargar/posiciones")
async def api_cargar_posiciones(archivo: UploadFile = File(...), confirmar: bool = False):
    from motor import plantillas
    from motor.modelo import PERFILES
    if not db.disponible():
        raise HTTPException(503, "No hay base de datos conectada.")
    o, act = _activos()
    try:
        por, errores = plantillas.leer_posiciones(await archivo.read(), o["meses"], _roles_cargados(o), {p["codigo"] for p in act})
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")
    resumen = [{"codigo": c, "posiciones": len(v), "dias": sum(i["dias"] for i in v)} for c, v in por.items()]
    resp = {"proyectos": resumen, "errores": errores, "guardado": False}
    if confirmar:
        if not por:
            raise HTTPException(400, "No hay filas válidas para cargar.")
        db.cargar_posiciones(por); resp["guardado"] = True
    return resp


@app.get("/api/estado")
def api_estado():
    return db.estado()


VISTAS = {"/": "index.html", "/criterios": "criterios.html", "/simulador": "simulador.html", "/colaboradores": "colaboradores.html", "/hoja": "hoja.html"}
for ruta, archivo in VISTAS.items():
    app.add_api_route(ruta, (lambda a=archivo: FileResponse(STATIC / a)), methods=["GET"], include_in_schema=False)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "favicon.ico")
