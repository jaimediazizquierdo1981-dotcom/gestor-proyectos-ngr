"""Plantilla Excel de colaboradores propios: descarga y validación de la carga."""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

COLUMNAS = ["Nombre", "Rol", "Área", "Cargo", "Días por semana", "Género"]
AREAS = ["Operaciones", "Continuidad del Negocio", "Nuevas Soluciones Digitales", "Data"]


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
