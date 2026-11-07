"""Log de auditoría: quién hizo qué y cuándo. Se guarda en gestor.logs y se repite en la salida estándar (logs de Render)."""
from __future__ import annotations

import json

from motor import db

ACCIONES = {
    ("POST", "/api/auth/login"): "Ingreso",
    ("POST", "/api/auth/cambiar"): "Cambio de contraseña",
    ("GET", "/logout"): "Salida",
    ("PUT", "/api/colaborador"): "Editar colaborador",
    ("PUT", "/api/asignaciones"): "Asignar días a persona",
    ("DELETE", "/api/asignaciones"): "Quitar persona de proyecto",
    ("PUT", "/api/necesidades"): "Editar posiciones del proyecto",
    ("PUT", "/api/proyecto"): "Editar montos del proyecto (Capex/Opex)",
    ("PUT", "/api/observaciones"): "Editar observaciones",
    ("POST", "/api/hoja/guardar"): "Guardar hoja de trabajo",
    ("DELETE", "/api/hoja/ficha"): "Eliminar ficha de proyecto",
    ("POST", "/api/cargar/colaboradores"): "Cargar plantilla de colaboradores",
    ("POST", "/api/cargar/posiciones"): "Cargar plantilla de posiciones",
    ("POST", "/api/usuarios"): "Crear usuario",
}
OCULTOS = {"password", "actual", "nueva", "captcha", "temporal", "clave"}


def accion(metodo: str, ruta: str) -> str | None:
    if (metodo, ruta) in ACCIONES:
        return ACCIONES[(metodo, ruta)]
    if ruta.startswith("/api/usuarios/"):
        if metodo == "POST" and ruta.endswith("/reset"):
            return "Resetear contraseña de usuario"
        if metodo == "PUT":
            return "Editar usuario"
    return None


def _limpio(x):
    if isinstance(x, dict):
        return {k: ("***" if k in OCULTOS else _limpio(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [_limpio(v) for v in x]
    return x


def _resumen_hoja(b: dict) -> dict:
    """La hoja completa es grande: se guarda qué proyectos se tocaron y su estado."""
    asig = b.get("asignaciones") or []
    por_proy = {}
    for a in asig:
        dias = sum(float(v or 0) for v in (a.get("dias") or {}).values())
        p = por_proy.setdefault(a.get("codigo"), {"personas": 0, "dias": 0.0})
        p["personas"] += 1; p["dias"] = round(p["dias"] + dias, 1)
    esc = {k: {c: v[c] for c in ("incluir", "on_hold", "prioridad", "inicio") if c in v} for k, v in (b.get("escenario") or {}).items()}
    return {"asignaciones_por_proyecto": por_proy, "escenario": {k: v for k, v in esc.items() if v},
            "capex": {k: v for k, v in (b.get("capex") or {}).items() if v}, "opex": {k: v for k, v in (b.get("opex") or {}).items() if v},
            "opex_meses": {k: v for k, v in (b.get("opex_meses") or {}).items() if v is not None}}


def registrar(request, accion_: str, quien: dict | None, cuerpo: bytes, estado: int) -> None:
    try:
        b = json.loads(cuerpo) if cuerpo else {}
    except ValueError:
        b = {}
    if not isinstance(b, dict):
        b = {"datos": b}
    usuario = (quien or {}).get("usuario") or ""
    if accion_ == "Ingreso":
        usuario = str(b.get("usuario", "")).strip().lower()
        if estado != 200:
            accion_ = "Ingreso fallido"
    detalle = _resumen_hoja(b) if accion_ == "Guardar hoja de trabajo" else _limpio(b)
    if request.query_params:
        detalle = {**dict(request.query_params), **detalle}
    if accion_.startswith(("Resetear", "Editar usuario")):
        detalle = {"usuario_afectado": request.url.path.split("/")[3], **detalle}
    if estado >= 400 and accion_ != "Ingreso fallido":
        detalle = {"resultado": f"error {estado}", **detalle}
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    texto = json.dumps(detalle, ensure_ascii=False, default=str)
    if len(texto) > 20000:
        texto = texto[:20000] + "…"
    print(f"AUDIT {json.dumps({'usuario': usuario, 'accion': accion_, 'estado': estado, 'detalle': texto[:1500]}, ensure_ascii=False)}", flush=True)
    if db.disponible():
        db.guardar_log(usuario, accion_, texto, ip, estado)
