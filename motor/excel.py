"""Exportación del Consolidado de proyectos a Excel (formato ejecutivo)."""
from datetime import datetime
from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.properties import Outline

AZUL, MORADO, GRIS, CLARO, BANDA = "1F3864", "5B2C83", "6B7280", "EEF2F9", "F7F8FB"
F = "Arial"
fino = Side(style="thin", color="D9DEE8")
BORDE = Border(left=fino, right=fino, top=fino, bottom=fino)
MON = '"S/ "#,##0;("S/ "#,##0);"-"'
DIAS = '#,##0.0;(#,##0.0);"-"'
PCT = '0.0%;(0.0%);"-"'
ETQ = {"cubierto": "Cubierto", "parcial": "Parcial", "no_cubierto": "No cubierto", "on_hold": "On hold",
       "sin_prioridad": "Sin prioridad", "excluido": "No activo"}
CORTO = {"Gestor de TI": "Gestor TI", "Lider Tecnico": "Líder Téc.", "Analista Funcional": "Anal. Func.",
         "Analista Técnico": "Anal. Téc.", "Desarrollador": "Desarrollador", "Analytics engineer": "Analytics Eng.",
         "Data Engineer": "Data Eng.", "Experto en Data": "Exp. Data", "Practicante Data": "Pract. Data",
         "Seguridad TI": "Seg. TI", "Demanda de Servicios": "Demanda Serv.", "Dev": "Desarrollador",
         "Especialista de Gobierno de Datos": "Gob. Datos"}


def _mes(m: str) -> str:
    p = pd.Period(m, "M")
    return ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][p.month - 1] + f"-{str(p.year)[2:]}"


def _cab(c, color=AZUL):
    c.font = Font(name=F, bold=True, color="FFFFFF", size=9)
    c.fill = PatternFill("solid", fgColor=color)
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = BORDE


def _titulo(ws, titulo, sub, ancho):
    ws["A1"] = titulo
    ws["A1"].font = Font(name=F, bold=True, size=16, color=AZUL)
    ws["A2"] = sub
    ws["A2"].font = Font(name=F, size=9, color=GRIS, italic=True)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ancho)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ancho)
    ws.sheet_view.showGridLines = False


def _activo(p) -> bool:
    return bool(p.get("incluir")) and not p.get("on_hold")


def _orden(p):
    return (0 if _activo(p) else 1, p.get("prioridad") or 999, p["codigo"])


def construir(R: dict, todos: bool) -> bytes:
    esT = lambda p: (p.get("tipo") or "Transformacional") != "No transformacional"
    P = sorted([p for p in R["proyectos"] if todos or esT(p)], key=_orden)
    M = R["meses"]
    PF = [f for f in R["perfiles"] if any((p["recursos"].get(f) or 0) > 0 for p in P)
          or any(x.get("tipo") != "tercero" and x["perfil"] == f for x in R["personas"])]
    TF = [t for t in R["terceros"] if any((p["terceros"].get(t) or 0) > 0 for p in P)]
    alcance = "Todos los proyectos (transformacionales y no transformacionales)" if todos else "Solo proyectos transformacionales"
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    wb = Workbook()

    # ------------------------------------------------------------------ Proyectos
    ws = wb.active
    ws.title = "Proyectos"
    cols = (["Prioridad", "PE", "OE.Cod", "OE.Descripción", "ID.PMO", "Programa", "Iniciativa", "Tipo", "Responsable PMO",
             "Líder TI", "Inicio", "Fin", "Meses"]
            + [CORTO.get(f, f) for f in PF] + ["Total internos"]
            + [CORTO.get(t, t) for t in TF] + ["Total terceros"]
            + ["Capex proyecto", "Capex terceros", "Capex total", "Opex mensual", "Opex hasta dic.", "Estado", "Cobertura", "Activo"])
    n = len(cols)
    _titulo(ws, "Consolidado de proyectos: Capex, Opex y recursos",
            f"Gestor de Proyectos NGR · {alcance} · Generado el {fecha} · Montos en soles sin IGV · Días-persona asignados en la Hoja de trabajo", n)
    # fila de grupos
    g = 4
    i_int, i_ter, i_inv = 14, 14 + len(PF) + 1, 14 + len(PF) + 1 + len(TF) + 1
    grupos = [(1, 13, "Proyecto", MORADO), (i_int, i_ter - 1, "Recursos internos (días)", AZUL),
              (i_ter, i_inv - 1, "Terceros (días)", "2E6B8F"), (i_inv, i_inv + 4, "Inversión y soporte (S/)", "1D7A4A"),
              (i_inv + 5, n, "Estado", GRIS)]
    for a, b, t, col in grupos:
        if b < a:
            continue
        ws.cell(g, a, t)
        for c in range(a, b + 1):
            _cab(ws.cell(g, c), col)
        if b > a:
            ws.merge_cells(start_row=g, start_column=a, end_row=g, end_column=b)
    h = 5
    for j, t in enumerate(cols, 1):
        ws.cell(h, j, t)
        _cab(ws.cell(h, j), next(col for a, b, _, col in grupos if a <= j <= max(a, b)))
    ws.row_dimensions[h].height = 32
    r0 = h + 1
    for k, p in enumerate(P):
        r = r0 + k
        act = _activo(p)
        ini = p.get("ini_hoja") or p.get("inicio_plan") or p.get("inicio")
        fin = p.get("fin_hoja") or p.get("fin_plan")
        fila = ([p.get("prioridad") or None, p.get("pe"), p.get("oe_cod"), p.get("oe_desc"), p["codigo"], p.get("programa"),
                 p["proyecto"], p.get("tipo") or "Transformacional", p.get("responsable"), p.get("lider_ti"),
                 _mes(ini) if ini else "", _mes(fin) if fin else "", p.get("duracion_meses")]
                + [p["recursos"].get(f) or 0 for f in PF] + [None]
                + [p["terceros"].get(t) or 0 for t in TF] + [None]
                + [p.get("capex") or 0, p.get("capex_terceros") or 0, None, p.get("opex_mensual") or 0,
                   p.get("opex_horizonte") or 0, ETQ.get(p["estado"], p["estado"]),
                   p.get("cobertura") if act else None, "Sí" if act else "No"])
        for j, v in enumerate(fila, 1):
            ws.cell(r, j, v)
        ws.cell(r, i_ter - 1, f"=SUM({L(i_int)}{r}:{L(i_ter - 2)}{r})")
        ws.cell(r, i_inv - 1, f"=SUM({L(i_ter)}{r}:{L(i_inv - 2)}{r})" if TF else 0)
        ws.cell(r, i_inv + 2, f"={L(i_inv)}{r}+{L(i_inv + 1)}{r}")
        for j in range(1, n + 1):
            c = ws.cell(r, j)
            c.font = Font(name=F, size=9, color="9CA3AF" if not act else "1F2430", bold=j in (5, 7, i_inv + 2))
            c.border = BORDE
            c.alignment = Alignment(vertical="center", horizontal="left" if j in (2, 4, 5, 6, 7, 8, 9, 10, n - 2) else "center",
                                    wrap_text=j in (4, 7))
            if k % 2:
                c.fill = PatternFill("solid", fgColor=BANDA)
            if i_int <= j < i_inv:
                c.number_format = DIAS
            elif i_inv <= j <= i_inv + 4:
                c.number_format = MON
        ws.cell(r, n - 1).number_format = PCT
    rT = r0 + len(P)
    ws.cell(rT, 1, f"Total proyectos activos")
    ws.merge_cells(start_row=rT, start_column=1, end_row=rT, end_column=13)
    act_rng = f"${L(n)}${r0}:${L(n)}${rT - 1}"
    for j in range(i_int, i_inv + 5):
        cl = L(j)
        ws.cell(rT, j, f'=SUMIFS({cl}{r0}:{cl}{rT - 1},{act_rng},"Sí")')
        ws.cell(rT, j).number_format = DIAS if j < i_inv else MON
    for j in range(1, n + 1):
        c = ws.cell(rT, j)
        c.font = Font(name=F, size=9, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.border = BORDE
        c.alignment = Alignment(horizontal="left" if j == 1 else "center", vertical="center")
    ws.cell(rT + 2, 1, "Notas: los totales suman solo los proyectos activos (columna Activo = Sí); las filas en gris son proyectos no activos. "
                       "Capex total = Capex del proyecto (equipos, asesorías, etc.) + Capex de terceros (días de terceros ÷ días hábiles del mes × costo mensual del cargo). "
                       "El Opex arranca el mes siguiente al fin del proyecto.").font = Font(name=F, size=8, italic=True, color=GRIS)
    anchos = {1: 8, 2: 22, 3: 8, 4: 30, 5: 11, 6: 22, 7: 34, 8: 15, 9: 18, 10: 18, 11: 8, 12: 8, 13: 7}
    for j in range(1, n + 1):
        ws.column_dimensions[L(j)].width = anchos.get(j, 12 if j >= i_inv else 9.5)
    ws.freeze_panes = ws.cell(r0, 8)
    ws.auto_filter.ref = f"A{h}:{L(n)}{rT - 1}"
    for j in (2, 3, 4, 9):   # alineamiento y responsable: agrupables
        ws.column_dimensions[L(j)].outlineLevel = 1
    ws.sheet_properties.outlinePr = Outline(summaryRight=False)
    ws.print_title_rows = f"{g}:{h}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ------------------------------------------------------------------ Capex mensual
    wc = wb.create_sheet("Capex mensual")
    cab = ["ID.PMO", "Iniciativa", "Tipo", "Activo", "Componente", "Capex total"] + [_mes(m) for m in M] + ["Fuera del horizonte"]
    nc = len(cab)
    _titulo(wc, "Capex mes a mes por proyecto",
            f"{alcance} · {_mes(M[0])} a {_mes(M[-1])} · Generado el {fecha} · Soles sin IGV · "
            "Clic en + / − del margen para abrir o cerrar el detalle por componente", nc)
    for j, t in enumerate(cab, 1):
        wc.cell(4, j, t)
        _cab(wc.cell(4, j), "1D7A4A" if j >= 6 else AZUL)
    wc.row_dimensions[4].height = 30
    r = 5
    filas_tot = []
    for k, p in enumerate(P):
        act = _activo(p)
        ini = p.get("ini_hoja") or p.get("inicio_plan") or p.get("inicio")
        fin = p.get("fin_hoja") or p.get("fin_plan") or ini
        rango = [str(x) for x in pd.period_range(ini, fin, freq="M")] if ini and fin and fin >= ini else []
        cap_p = float(p.get("capex") or 0)
        por_mes = cap_p / len(rango) if rango else 0.0
        adic = [por_mes if m in rango else 0 for m in M]
        fuera_a = cap_p - sum(adic)
        ter = [float((p.get("capex_terceros_mes") or {}).get(m, 0)) for m in M]
        fuera_t = float(p.get("capex_terceros") or 0) - sum(ter)
        rp, ra, rt = r, r + 1, r + 2
        for rr, comp, vals, fuera in ((ra, "Capex del proyecto (equipos, asesorías, etc.)", adic, fuera_a),
                                      (rt, "Capex de terceros (días asignados × costo del cargo)", ter, fuera_t)):
            wc.cell(rr, 4, "Sí" if act else "No")
            wc.cell(rr, 5, comp)
            for i, v in enumerate(vals):
                wc.cell(rr, 7 + i, round(v, 2))
            wc.cell(rr, nc, round(fuera, 2) if abs(fuera) > 0.5 else 0)
            wc.cell(rr, 6, f"=SUM({L(7)}{rr}:{L(nc)}{rr})")
            wc.row_dimensions[rr].outlineLevel = 1
            for j in range(1, nc + 1):
                c = wc.cell(rr, j)
                c.font = Font(name=F, size=8, color="9CA3AF" if not act else "4B5563", italic=j == 5)
                c.border = BORDE
                c.number_format = MON if j >= 6 else "General"
                c.alignment = Alignment(horizontal="left" if j == 5 else "center", indent=1 if j == 5 else 0)
        wc.cell(rp, 1, p["codigo"])
        wc.cell(rp, 2, p["proyecto"])
        wc.cell(rp, 3, p.get("tipo") or "Transformacional")
        wc.cell(rp, 4, "Sí" if act else "No")
        wc.cell(rp, 5, "Total proyecto")
        for j in range(6, nc + 1):
            wc.cell(rp, j, f"={L(j)}{ra}+{L(j)}{rt}")
        for j in range(1, nc + 1):
            c = wc.cell(rp, j)
            c.font = Font(name=F, size=9, bold=True, color="9CA3AF" if not act else "1F2430")
            c.fill = PatternFill("solid", fgColor=CLARO if act else "F3F4F6")
            c.border = BORDE
            c.number_format = MON if j >= 6 else "General"
            c.alignment = Alignment(horizontal="left" if j in (1, 2, 3, 5) else "center", vertical="center")
        filas_tot.append(rp)
        r += 3
    rT2 = r
    wc.cell(rT2, 1, "Total Capex · proyectos activos")
    wc.merge_cells(start_row=rT2, start_column=1, end_row=rT2, end_column=5)
    for j in range(6, nc + 1):
        cl = L(j)
        wc.cell(rT2, j, f'=SUMIFS({cl}5:{cl}{rT2 - 1},$D$5:$D${rT2 - 1},"Sí",$E$5:$E${rT2 - 1},"Total proyecto")')
    for j in range(1, nc + 1):
        c = wc.cell(rT2, j)
        c.font = Font(name=F, size=9, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.border = BORDE
        c.number_format = MON
        c.alignment = Alignment(horizontal="left" if j == 1 else "center")
    for rr, (comp, etq) in enumerate((("Capex del proyecto (equipos, asesorías, etc.)", "   · Capex del proyecto"),
                                     ("Capex de terceros (días asignados × costo del cargo)", "   · Capex de terceros")), rT2 + 1):
        wc.cell(rr, 1, etq)
        wc.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=5)
        for j in range(6, nc + 1):
            cl = L(j)
            wc.cell(rr, j, f'=SUMIFS({cl}5:{cl}{rT2 - 1},$D$5:$D${rT2 - 1},"Sí",$E$5:$E${rT2 - 1},"{comp}")')
            wc.cell(rr, j).number_format = MON
        for j in range(1, nc + 1):
            c = wc.cell(rr, j)
            c.font = Font(name=F, size=8, bold=True, color=AZUL)
            c.fill = PatternFill("solid", fgColor=CLARO)
            c.border = BORDE
    wc.cell(rT2 + 4, 1, "Supuestos: el Capex del proyecto (monto de la ficha) se reparte en partes iguales entre los meses del proyecto "
                         "(del primer al último mes con días asignados en la Hoja; si no tiene, según su plan). El Capex de terceros sale de los días "
                         "asignados a terceros cada mes ÷ días hábiles del mes × costo mensual del cargo (Criterios). "
                         "'Fuera del horizonte' = montos de meses posteriores al último mes del horizonte.").font = Font(name=F, size=8, italic=True, color=GRIS)
    for j, w in enumerate([11, 36, 15, 7, 40, 14] + [11] * len(M) + [12], 1):
        wc.column_dimensions[L(j)].width = w
    wc.freeze_panes = "G5"
    wc.sheet_properties.outlinePr = Outline(summaryBelow=False)
    wc.page_setup.orientation = "landscape"
    wc.sheet_properties.pageSetUpPr.fitToPage = True
    wc.page_setup.fitToHeight = 0

    # ------------------------------------------------------------------ Resumen
    wr = wb.create_sheet("Resumen", 0)
    _titulo(wr, "Portafolio de proyectos 2027 · Resumen ejecutivo", f"{alcance} · Generado el {fecha} · Soles sin IGV", 4)
    pr, pc = "Proyectos", "'Capex mensual'"
    rng = lambda col: f"{pr}!{col}{r0}:{col}{rT - 1}"
    act_c = rng(L(n))
    est_c = rng(L(n - 2))
    kp = [("Proyectos en el escenario", f'=COUNTIF({act_c},"Sí")&" de "&COUNTA({rng("E")})', "General"),
          ("Cubiertos al 100%", f'=COUNTIFS({act_c},"Sí",{est_c},"Cubierto")&" de "&COUNTIF({act_c},"Sí")', "General"),
          ("Capex total", f"={pr}!{L(i_inv + 2)}{rT}", MON),
          ("Capex cubierto", f'=SUMIFS({rng(L(i_inv + 2))},{act_c},"Sí",{est_c},"Cubierto")', MON),
          ("Opex mensual (run rate)", f"={pr}!{L(i_inv + 3)}{rT}", MON),
          ("Opex anual (run rate)", f"={pr}!{L(i_inv + 3)}{rT}*12", MON),
          ("Opex dentro del horizonte", f"={pr}!{L(i_inv + 4)}{rT}", MON),
          ("Días internos asignados", f"={pr}!{L(i_ter - 1)}{rT}", DIAS),
          ("Días de terceros asignados", f"={pr}!{L(i_inv - 1)}{rT}", DIAS)]
    _cab(wr.cell(4, 2, "Indicador"))
    _cab(wr.cell(4, 3, "Valor"))
    for i, (t, fml, fmt) in enumerate(kp, 5):
        a, b = wr.cell(i, 2, t), wr.cell(i, 3, fml)
        a.font = Font(name=F, size=10, color="1F2430")
        b.font = Font(name=F, size=11, bold=True, color=AZUL)
        b.number_format = fmt
        b.alignment = Alignment(horizontal="right")
        for c in (a, b):
            c.border = BORDE
            if i % 2:
                c.fill = PatternFill("solid", fgColor=BANDA)
    rr = 5 + len(kp) + 2
    _cab(wr.cell(rr, 2, "Capex por mes (proyectos activos)"), "1D7A4A")
    _cab(wr.cell(rr, 3, "S/"), "1D7A4A")
    for i, m in enumerate(M):
        wr.cell(rr + 1 + i, 2, _mes(m)).font = Font(name=F, size=10)
        c = wr.cell(rr + 1 + i, 3, f"={pc}!{L(7 + i)}{rT2}")
        c.number_format = MON
        c.font = Font(name=F, size=10)
        for x in (wr.cell(rr + 1 + i, 2), c):
            x.border = BORDE
    fz = rr + 1 + len(M)
    wr.cell(fz, 2, "Fuera del horizonte").font = Font(name=F, size=10, italic=True)
    c = wr.cell(fz, 3, f"={pc}!{L(nc)}{rT2}")
    c.number_format = MON
    tt = fz + 1
    wr.cell(tt, 2, "Total")
    wr.cell(tt, 3, f"=SUM(C{rr + 1}:C{fz})").number_format = MON
    for x in (wr.cell(fz, 2), wr.cell(fz, 3)):
        x.border = BORDE
    for x in (wr.cell(tt, 2), wr.cell(tt, 3)):
        x.font = Font(name=F, size=10, bold=True, color="FFFFFF")
        x.fill = PatternFill("solid", fgColor=AZUL)
        x.border = BORDE
    wr.column_dimensions["A"].width = 3
    wr.column_dimensions["B"].width = 34
    wr.column_dimensions["C"].width = 22
    wr.column_dimensions["D"].width = 3
    # gráfico de Capex mensual
    from openpyxl.chart import BarChart, Reference
    ch = BarChart()
    ch.type = "col"
    ch.title = "Capex mensual (S/)"
    ch.y_axis.numFmt = "#,##0"
    ch.add_data(Reference(wr, min_col=3, min_row=rr, max_row=rr + len(M)), titles_from_data=True)
    ch.set_categories(Reference(wr, min_col=2, min_row=rr + 1, max_row=rr + len(M)))
    ch.legend = None
    ch.roundedCorners = False
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ch.title.overlay = False
    ch.height, ch.width = 8, 17
    ch.series[0].graphicalProperties.solidFill = "1D7A4A"
    wr.add_chart(ch, "E4")

    wr.page_setup.orientation = "landscape"
    wr.sheet_properties.pageSetUpPr.fitToPage = True
    for w in wb.worksheets:
        w.sheet_view.zoomScale = 90
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()
