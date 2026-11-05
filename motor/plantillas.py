"""Plantilla Excel de colaboradores propios: descarga y validación de la carga."""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

COLUMNAS = ["Nombre", "Rol", "Área", "Cargo", "Días por semana", "Género"]
AREAS = ["Estrategia", "Operaciones", "Continuidad del Negocio", "Nuevas Soluciones Digitales", "Data"]
ALIAS_ROL = {"practicantte data": "Practicante Data", "lider técnico": "Lider Tecnico", "líder técnico": "Lider Tecnico", "analytics engineer": "Analytics engineer", "demanda": "Demanda de Servicios"}


def plantilla(equipo: list[dict], roles: list[str]) -> bytes:
    wb = Workbook(); ws = wb.active; ws.title = "Colaboradores"
    ws.append(COLUMNAS)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3864"); c.alignment = Alignment(horizontal="center")
    for e in equipo:
        if e.get("tipo") == "tercero":
            continue
        ws.append([e["persona"], e["perfil"], e.get("area") or "", e.get("cargo") or "", float(e.get("dias_semana") or 5), e.get("genero") or ""])
    for col, w in zip("ABCDEF", [28, 20, 28, 34, 16, 12]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    li = wb.create_sheet("Listas")
    li.append(["Roles válidos", "Áreas", "Género"])
    for i in range(max(len(roles), len(AREAS), 2)):
        li.append([roles[i] if i < len(roles) else None, AREAS[i] if i < len(AREAS) else None, ["Mujer", "Hombre"][i] if i < 2 else None])
    n = 2000
    for rng, ref in [(f"B2:B{n}", f"Listas!$A$2:$A${len(roles) + 1}"), (f"C2:C{n}", f"Listas!$B$2:$B${len(AREAS) + 1}"), (f"F2:F{n}", "Listas!$C$2:$C$3")]:
        dv = DataValidation(type="list", formula1=ref, allow_blank=True); dv.add(rng); ws.add_data_validation(dv)
    dv = DataValidation(type="decimal", operator="between", formula1="0", formula2="6", allow_blank=True)
    dv.error = "Entre 0 y 6 días"; dv.add(f"E2:E{n}"); ws.add_data_validation(dv)
    ins = wb.create_sheet("Instrucciones")
    for t in ["Una fila por colaborador propio (no terceros).", "Nombre: único; si ya existe se actualiza.",
              "Rol: elegir de la lista (define qué posiciones puede cubrir).", "Días por semana: 5 = tiempo completo, 2,5 = medio tiempo.",
              "Modo 'Actualizar': agrega y actualiza. Modo 'Reemplazar': además quita a los colaboradores que no estén en el archivo."]:
        ins.append([t])
    ins.column_dimensions["A"].width = 100
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def leer(contenido: bytes, roles: list[str]) -> tuple[list[dict], list[str]]:
    ws = load_workbook(io.BytesIO(contenido), data_only=True).worksheets[0]
    filas, errores, vistos = [], [], set()
    cab = [str(c.value or "").strip().lower() for c in ws[1]]
    idx = {k: cab.index(k.lower()) for k in COLUMNAS if k.lower() in cab}
    if "Nombre" not in idx or "Rol" not in idx:
        return [], ["La hoja debe tener las columnas Nombre y Rol (usa la plantilla)."]
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        g = lambda k: (row[idx[k]] if k in idx and idx[k] < len(row) else None)
        nombre = str(g("Nombre") or "").strip()
        if not nombre and not g("Rol"):
            continue
        rol = str(g("Rol") or "").strip()
        rol = ALIAS_ROL.get(rol.lower(), next((r for r in roles if r.lower() == rol.lower()), rol))
        try:
            dias = float(g("Días por semana") if g("Días por semana") not in (None, "") else 5)
        except (TypeError, ValueError):
            dias = -1
        if not nombre:
            errores.append(f"Fila {n}: falta el nombre."); continue
        if nombre.lower() in vistos:
            errores.append(f"Fila {n}: {nombre} está repetido."); continue
        if rol not in roles:
            errores.append(f"Fila {n}: el rol '{rol}' no es válido."); continue
        if not 0 <= dias <= 6:
            errores.append(f"Fila {n}: días por semana debe estar entre 0 y 6."); continue
        vistos.add(nombre.lower())
        filas.append({"persona": nombre, "perfil": rol, "area": str(g("Área") or "").strip(), "cargo": str(g("Cargo") or "").strip(),
                      "dias_semana": dias, "genero": str(g("Género") or "").strip()})
    return filas, errores


# ---------- plantilla de posiciones por proyecto: días por rol y su distribución mensual
def _hoja_pos(ws, meses, roles, codigos):
    from openpyxl.utils import get_column_letter
    cab = ["Código", "Proyecto", "Rol"] + [_mes(m) for m in meses] + ["Total días"]
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3864"); c.alignment = Alignment(horizontal="center")
    ws.column_dimensions["A"].width = 9; ws.column_dimensions["B"].width = 38; ws.column_dimensions["C"].width = 20
    for k in range(len(meses)):
        ws.column_dimensions[get_column_letter(4 + k)].width = 8
    ws.column_dimensions[get_column_letter(4 + len(meses))].width = 11
    ws.freeze_panes = "D2"
    return cab


_MES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
def _mes(m: str) -> str:
    return f"{_MES[int(m[5:]) - 1]}-{m[2:4]}"


def _filas_pos(ws, filas, meses):
    from openpyxl.utils import get_column_letter
    for f in filas:
        n = ws.max_row + 1
        ws.append([f["codigo"], f["proyecto"], f["perfil"]] + [f["dist"].get(m) or None for m in meses] + [None])
        ws.cell(n, 4 + len(meses)).value = f"=SUM({get_column_letter(4)}{n}:{get_column_letter(3 + len(meses))}{n})"
        ws.cell(n, 4 + len(meses)).font = Font(bold=True)


def plantilla_posiciones(proyectos: list[dict], necesidades: dict, meses: list[str], roles: list[str]) -> bytes:
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = "Posiciones"
    _hoja_pos(ws, meses, roles, [p["codigo"] for p in proyectos])
    filas = []
    for p in proyectos:
        for i in necesidades.get(p["codigo"], []):
            dist = i.get("dist") or {}
            if not dist:   # sin distribución guardada: promedio simple en su rango
                mm = [m for m in meses if (i.get("desde") or p["inicio_plan"]) <= m <= (i.get("hasta") or p["fin_plan"])]
                base = round(float(i["dias"]) / len(mm) * 2) / 2 if mm else 0
                dist = {m: base for m in mm}
                if mm: dist[mm[-1]] = round((float(i["dias"]) - base * (len(mm) - 1)) * 2) / 2
            filas.append({"codigo": p["codigo"], "proyecto": p["proyecto"], "perfil": i["perfil"], "dist": dist})
    _filas_pos(ws, filas, meses)
    for p in proyectos:   # una fila vacía por proyecto para que agreguen roles
        if not necesidades.get(p["codigo"]):
            _filas_pos(ws, [{"codigo": p["codigo"], "proyecto": p["proyecto"], "perfil": None, "dist": {}}], meses)
    n = 2000
    li = wb.create_sheet("Listas")
    li.append(["Roles válidos", "Proyectos activos", "Nombre"])
    for k in range(max(len(roles), len(proyectos))):
        li.append([roles[k] if k < len(roles) else None, proyectos[k]["codigo"] if k < len(proyectos) else None,
                   proyectos[k]["proyecto"] if k < len(proyectos) else None])
    li.column_dimensions["A"].width = 20; li.column_dimensions["C"].width = 40
    for rng, ref in [(f"C2:C{n}", f"Listas!$A$2:$A${len(roles) + 1}"), (f"A2:A{n}", f"Listas!$B$2:$B${max(len(proyectos), 1) + 1}")]:
        dv = DataValidation(type="list", formula1=ref, allow_blank=True); dv.add(rng); ws.add_data_validation(dv)
    dv = DataValidation(type="decimal", operator="between", formula1="0", formula2="31", allow_blank=True)
    dv.error = "Días del mes entre 0 y 31"; dv.add(f"D2:{get_column_letter(3 + len(meses))}{n}"); ws.add_data_validation(dv)
    # hoja de ejemplo
    ej = wb.create_sheet("Ejemplo")
    _hoja_pos(ej, meses, roles, [])
    ejm = {"PM": {meses[3]: 20, meses[4]: 20}, "Desarrollador": {meses[2]: 10, meses[3]: 15, meses[4]: 15, meses[5]: 10},
           "QA": {meses[4]: 6, meses[5]: 8}, "Seguridad TI": {meses[5]: 4}}
    ejm = {r: d for r, d in ejm.items() if r in roles} or {roles[0]: {meses[3]: 20, meses[4]: 20}}
    _filas_pos(ej, [{"codigo": "PRY-27-XX", "proyecto": "Proyecto de ejemplo", "perfil": r, "dist": dd} for r, dd in ejm.items()], meses)
    r0, d0 = next(iter(ejm.items()))
    ej.cell(ej.max_row + 2, 1).value = f"Ejemplo: {r0} necesita {sum(d0.values()):g} días repartidos por mes como se ve en su fila. El total se calcula solo."
    ins = wb.create_sheet("Instrucciones")
    for t in ["Hoja 'Posiciones': una fila por proyecto y rol. Escribe los días-persona de cada mes; el total se calcula solo.",
              "Código: proyecto activo (lista en 'Listas'). Rol: elegir de la lista.",
              "Los meses en blanco o en 0 no se usan. El rango de la posición va del primer al último mes con días.",
              "Al cargar, se reemplazan las posiciones de los proyectos que vengan en el archivo; los demás no se tocan.",
              "Mira la hoja 'Ejemplo'."]:
        ins.append([t])
    ins.column_dimensions["A"].width = 110
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def leer_posiciones(contenido: bytes, meses: list[str], roles: list[str], activos: set[str]) -> tuple[dict, list[str]]:
    ws = load_workbook(io.BytesIO(contenido), data_only=False).worksheets[0]
    cab = [str(c.value or "").strip() for c in ws[1]]
    etiquetas = {_mes(m): m for m in meses}
    col_mes = {k: etiquetas[v] for k, v in enumerate(cab) if v in etiquetas}
    if not cab or cab[0].lower() != "código" or "Rol" not in cab:
        return {}, ["Usa la plantilla: la primera hoja debe tener Código, Proyecto, Rol y los meses."]
    i_rol = cab.index("Rol")
    out, errores, vistos = {}, [], set()
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        cod = str(row[0] or "").strip().upper(); rol = str(row[i_rol] or "").strip()
        dist = {}
        for k, m in col_mes.items():
            v = row[k] if k < len(row) else None
            if v in (None, "") or (isinstance(v, str) and v.startswith("=")):
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                errores.append(f"Fila {n}: valor no numérico en {_mes(m)}."); dist = None; break
            if v < 0 or v > 31:
                errores.append(f"Fila {n}: {_mes(m)} debe estar entre 0 y 31."); dist = None; break
            if v > 0:
                dist[m] = round(v * 2) / 2
        if dist is None:
            continue
        if not cod and not rol and not dist:
            continue
        if cod not in activos:
            errores.append(f"Fila {n}: el proyecto '{cod}' no está activo o no existe."); continue
        if not rol:
            if dist: errores.append(f"Fila {n}: falta el rol.")
            else: out.setdefault(cod, [])   # fila vacía: deja el proyecto sin posiciones
            continue
        if rol not in roles:
            errores.append(f"Fila {n}: el rol '{rol}' no es válido."); continue
        if (cod, rol) in vistos:
            errores.append(f"Fila {n}: {cod} ya tiene una fila para {rol}."); continue
        vistos.add((cod, rol))
        if not dist:
            out.setdefault(cod, []); continue
        mm = sorted(dist)
        out.setdefault(cod, []).append({"perfil": rol, "dias": sum(dist.values()), "desde": mm[0], "hasta": mm[-1], "dist": dist})
    return out, errores
