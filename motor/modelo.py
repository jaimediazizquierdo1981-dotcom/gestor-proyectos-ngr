"""Motor del gestor de proyectos.

1. Capacidad neta por persona y mes (días hábiles - feriados - vacaciones - reserva para operación).
2. Distribución del esfuerzo de cada proyecto en el tiempo (uniforme o curva de Rayleigh / Norden-Putnam).
3. Asignación con recursos limitados por prioridad (RCPSP, esquema serial con regla de prioridad).
4. Riesgo: Monte Carlo con esfuerzo PERT-Beta -> probabilidad de que cada proyecto quede cubierto.
5. Días proyectados vs. capacidad real que el equipo le puede asignar a cada proyecto.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
PERFILES = ["PM", "Lider Tecnico", "Arquitecto", "Analista Funcional", "Analista Técnico", "Desarrollador", "Analytics engineer", "Data Engineer", "Practicante Data", "QA", "Infra", "Devops", "Seguridad TI", "Soporte", "Demanda de Servicios"]
TERCEROS = ["Dev", "QA", "Analista Funcional", "Analista Técnico", "Seguridad TI", "Devops", "Soporte", "Demanda de Servicios"]  # días de terceros: no consumen capacidad interna


# ---------------------------------------------------------------- utilidades
def _fusionar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusionar(out[k], v)
        else:
            out[k] = v
    return out


def activo(x: dict) -> bool:
    """Cuenta en el cálculo: incluido y no está en pausa (on hold)."""
    return bool(x.get("incluir", False)) and not x.get("on_hold", False)


def meses(inicio: str, fin: str) -> list[str]:
    return [str(p) for p in pd.period_range(inicio, fin, freq="M")]


def _idx(m: str, lista: list[str]) -> int:
    return lista.index(m) if m in lista else (-1 if m < lista[0] else len(lista))


def cargar_base() -> dict:
    from motor import db
    if db.disponible():
        b = db.leer()
        archivo = json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))
        b["parametros"] = _fusionar(archivo, b["parametros"] or {})   # parámetros nuevos del archivo + los guardados
        b["perfiles"] = PERFILES
        b["origen"] = "base de datos"
        return b
    params = json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))
    proyectos = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str, "oe_cod": str}).fillna("")
    equipo = pd.read_csv(DATA / "equipo.csv")
    feriados = pd.read_csv(DATA / "feriados.csv")
    return {"parametros": params, "proyectos": proyectos.to_dict(orient="records"),
            "equipo": equipo.to_dict(orient="records"), "feriados": feriados.to_dict(orient="records"),
            "perfiles": PERFILES, "origen": "archivos"}


# ---------------------------------------------------------------- 1. capacidad
def capacidad(equipo: list[dict], feriados: list[dict], p: dict, lista: list[str]) -> dict:
    """Días netos disponibles para proyectos, por persona y mes."""
    c = p["capacidad"]
    fer = {pd.Timestamp(f["fecha"]).date() for f in feriados} if c.get("descontar_feriados", True) else set()
    out = {}
    for e in equipo:
        if e.get("tipo") == "tercero":   # terceros: 22 días hábiles al mes menos los feriados del mes (sin vacaciones ni reserva)
            base = float(c.get("dias_habiles_mes_tercero", 22))
            out[e["persona"]] = np.array([
                max(base - sum(1 for d in pd.bdate_range(pd.Period(m, "M").start_time, pd.Period(m, "M").end_time) if d.date() in fer), 0)
                for m in lista])
            continue
        fila = []
        fd = float(e.get("dias_semana", 5) or 5) / 5   # fracción de cada día hábil que trabaja
        for m in lista:
            per = pd.Period(m, "M")
            dias = pd.bdate_range(per.start_time, per.end_time)
            habiles = sum(1 for d in dias if d.date() not in fer)
            vac = float(c.get("dias_vacaciones_anual", 0)) / 12
            bruto = max(habiles - vac, 0) * fd
            fila.append(bruto * (1 - float(c.get("reserva_operacion_pct", 0))))
        out[e["persona"]] = np.array(fila)
    return out


# ---------------------------------------------------------------- 2. distribución del esfuerzo
def pesos(n: int, metodo: str, pico: float) -> np.ndarray:
    """Fracción del esfuerzo total que cae en cada mes del proyecto."""
    if n <= 0:
        return np.array([])
    if metodo != "rayleigh" or n == 1:
        return np.full(n, 1 / n)
    # Norden-Rayleigh: F(t) = 1 - exp(-t^2 / (2 td^2)), td = mes pico; se normaliza para cerrar en el último mes
    td = max(pico, 0.05) * n
    F = lambda t: 1 - math.exp(-(t ** 2) / (2 * td ** 2))
    w = np.array([F(i + 1) - F(i) for i in range(n)])
    return w / w.sum()


def plan_proyecto(pr: dict, p: dict, lista: list[str], desplazo: int = 0, factor: float = 1.0) -> dict:
    """Días requeridos por perfil y mes (vector sobre el horizonte)."""
    ini = pd.Period(pr["inicio"], "M") + desplazo
    fin = pd.Period(pr["fin"], "M") + desplazo
    pm = [str(x) for x in pd.period_range(ini, fin, freq="M")]
    w = pesos(len(pm), p["estimacion"]["distribucion"], float(p["estimacion"].get("pico_rayleigh", 0.4)))
    k = (1 + float(p["estimacion"].get("contingencia_pct", 0))) * factor
    req = {}
    for perfil in PERFILES:
        v = np.zeros(len(lista))
        for j, m in enumerate(pm):
            i = _idx(m, lista)
            if 0 <= i < len(lista):
                v[i] += float(pr.get(perfil, 0) or 0) * k * w[j]
        req[perfil] = v
    return req


# ---------------------------------------------------------------- 3. asignación con recursos limitados
def asignar(proyectos: list[dict], equipo: list[dict], cap: dict, p: dict, lista: list[str], factores=None,
            detalle=True) -> dict:
    cob = p["cobertura"]
    libre = {k: v.copy() for k, v in cap.items()}
    por_perfil = {pf: [e["persona"] for e in equipo if e["perfil"] == pf and e.get("tipo") != "tercero"] for pf in PERFILES}
    activos = sorted([x for x in proyectos if activo(x)], key=lambda x: float(x.get("prioridad", 99)))
    res, asign = {}, {}
    for pr in activos:
        f = (factores or {}).get(pr["codigo"], 1.0)
        mejor = None
        for d in range(0, int(cob.get("meses_desplazamiento_max", 0)) + 1):
            req = plan_proyecto(pr, p, lista, d, f)
            # total del proyecto completo: lo que cae fuera del horizonte cuenta como no cubierto
            tot = sum(float(pr.get(pf, 0) or 0) for pf in PERFILES) * (1 + float(p["estimacion"].get("contingencia_pct", 0))) * f
            cubre = 0.0
            for pf, v in req.items():
                disp = sum(libre[n] for n in por_perfil[pf]) if por_perfil[pf] else np.zeros(len(lista))
                cubre += np.minimum(v, disp).sum()
            ratio = cubre / tot if tot else 1.0
            if mejor is None or ratio > mejor[0] + 1e-9:
                mejor = (ratio, d, req, tot)
            if ratio >= float(cob["umbral_cubierto"]):
                break
        ratio, d, req, tot = mejor
        estado = ("cubierto" if ratio >= float(cob["umbral_cubierto"]) else
                  "parcial" if ratio >= float(cob["umbral_parcial"]) else "no_cubierto")
        # un proyecto no cubierto no consume capacidad (queda en espera); cubierto o parcial sí
        if estado != "no_cubierto":
            for pf, v in req.items():
                gente = por_perfil[pf]
                for i in range(len(lista)):
                    if v[i] <= 0 or not gente:
                        continue
                    disp = np.array([libre[n][i] for n in gente])
                    usar = min(v[i], disp.sum())
                    if usar <= 0:
                        continue
                    reparto = disp / disp.sum() * usar
                    for n, h in zip(gente, reparto):
                        libre[n][i] -= h
                        if detalle and h > 0.01:
                            asign.setdefault(n, {}).setdefault(lista[i], {})[pr["codigo"]] = round(float(h), 2)
        res[pr["codigo"]] = {"estado": estado, "cobertura": round(float(ratio), 4), "desplazo_meses": d,
                             "dias_requeridos": round(float(tot), 1),
                             "req_mes": {pf: v.round(1).tolist() for pf, v in req.items()} if detalle else None}
    for pr in proyectos:
        if not activo(pr):
            res[pr["codigo"]] = {"estado": "excluido" if not pr.get("incluir", False) else "on_hold", "cobertura": 0, "desplazo_meses": 0,
                                 "dias_requeridos": 0, "req_mes": None}
    return {"resultado": res, "asignacion": asign, "libre": libre}


# ---------------------------------------------------------------- 4. Monte Carlo PERT
def pert_muestras(rng, a, m, b, n):
    """PERT-Beta: alfa = 1 + 4(m-a)/(b-a), beta = 1 + 4(b-m)/(b-a)."""
    if b <= a:
        return np.full(n, m)
    al = 1 + 4 * (m - a) / (b - a)
    be = 1 + 4 * (b - m) / (b - a)
    return a + rng.beta(al, be, n) * (b - a)


def montecarlo(proyectos, equipo, cap, p, lista) -> dict:
    r = p["riesgo"]
    n = int(r.get("simulaciones", 400))
    rng = np.random.default_rng(int(r.get("semilla", 2027)))
    a, m, b = float(r["pert_optimista"]), float(r["pert_probable"]), float(r["pert_pesimista"])
    act = [x for x in proyectos if activo(x)]
    mu = (a + 4 * m + b) / 6
    if n <= 0 or not act:
        return {"prob": {}, "dist_cubiertos": [], "n": 0, "media_factor": mu}
    muestras = {x["codigo"]: pert_muestras(rng, a, m, b, n) for x in act}
    cuenta = {x["codigo"]: 0 for x in act}
    dist = []
    for s in range(n):
        out = asignar(proyectos, equipo, cap, p, lista, {k: v[s] for k, v in muestras.items()}, detalle=False)["resultado"]
        c = 0
        for k in cuenta:
            if out[k]["estado"] == "cubierto":
                cuenta[k] += 1
                c += 1
        dist.append(c)
    return {"prob": {k: v / n for k, v in cuenta.items()},
            "dist_cubiertos": np.bincount(dist, minlength=len(act) + 1).tolist(), "n": n, "media_factor": mu}


# ---------------------------------------------------------------- 6. hoja de trabajo (manda sobre el modelo automático)
def _medio(x: float) -> float:
    return math.floor(x * 2 + 0.5) / 2


def desde_hoja(auto: dict, proyectos, equipo, cap, p, lista, hoja: dict) -> dict:
    """Cobertura y uso a partir de lo asignado a mano en la Hoja de trabajo."""
    cob = p["cobertura"]
    perfil_de = {e["persona"]: e["perfil"] for e in equipo}
    activos = {x["codigo"] for x in proyectos if activo(x)}
    en_pausa = {x["codigo"] for x in proyectos if x.get("incluir", False) and x.get("on_hold")}
    asign, libre, pausa = {}, {k: v.copy() for k, v in cap.items()}, {}
    for r in hoja["asig"]:
        if r["codigo"] in en_pausa and r["persona"] in perfil_de:   # on hold: se guarda para mostrarlo, no descuenta
            for mes, d in (r["dias"] or {}).items():
                if mes in lista and float(d) > 0:
                    pausa.setdefault(r["persona"], {}).setdefault(mes, {})[r["codigo"]] = float(d)
            continue
        if r["codigo"] not in activos or r["persona"] not in perfil_de:
            continue
        for mes, d in (r["dias"] or {}).items():
            if mes in lista and float(d) > 0:
                asign.setdefault(r["persona"], {}).setdefault(mes, {})[r["codigo"]] = float(d)
                libre[r["persona"]][lista.index(mes)] -= float(d)
    res = {}
    for pr in proyectos:
        c = pr["codigo"]
        req_mes = plan_proyecto(pr, p, lista)
        if not activo(pr):   # excluido u on hold: igual se devuelve su plan para poder verlo en la hoja
            res[c] = {**auto["resultado"][c], "req_mes": {pf: v.round(1).tolist() for pf, v in req_mes.items()}}
            continue
        if c in hoja["nec"]:
            req = {i["perfil"]: float(i["dias"]) for i in hoja["nec"][c]}
        else:   # aún no se definieron posiciones en la hoja: vacío
            req = {}
        asig_pf = {pf: 0.0 for pf in PERFILES}
        for n, ms in asign.items():
            for d in ms.values():
                asig_pf[perfil_de[n]] += d.get(c, 0)
        tot = sum(req.values())
        ratio = sum(min(asig_pf.get(pf, 0), v) for pf, v in req.items()) / tot if tot else 0.0
        estado = ("cubierto" if ratio >= float(cob["umbral_cubierto"]) else
                  "parcial" if ratio > 0 and ratio >= float(cob["umbral_parcial"]) else
                  "parcial" if ratio > 0 else "no_cubierto")
        res[c] = {"estado": estado, "cobertura": round(ratio, 4), "desplazo_meses": 0, "dias_requeridos": round(tot, 1),
                  "req_mes": {pf: v.round(1).tolist() for pf, v in req_mes.items()},
                  "req_hoja": req, "asig_hoja": asig_pf}
    return {"resultado": res, "asignacion": asign, "libre": libre, "pausa": pausa}


# ---------------------------------------------------------------- orquestador
def ejecutar(entrada: dict | None = None, con_mc: bool = True) -> dict:
    entrada = entrada or {}
    base = cargar_base()
    p = _fusionar(base["parametros"], entrada.get("parametros", {}))
    lista = meses(p["horizonte"]["inicio"], p["horizonte"]["fin"])
    from motor import db as _db
    guardado = _db.leer_escenario()["data"] if _db.disponible() else {}
    escenario = {k: {**guardado.get(k, {}), **entrada.get("escenario", {}).get(k, {})}
                 for k in set(guardado) | set(entrada.get("escenario", {}))}  # {codigo: {incluir, on_hold, inicio, prioridad}}
    proyectos = []
    for pr in entrada.get("proyectos") or base["proyectos"]:
        pr = {**pr, **escenario.get(pr["codigo"], {})}
        pr.setdefault("incluir", False)   # los proyectos se activan uno a uno desde la Hoja de trabajo
        pr.setdefault("on_hold", False)
        if pr["inicio"] != base_inicio(base, pr["codigo"]) and "fin" not in escenario.get(pr["codigo"], {}):
            # mover el inicio mantiene la duración original
            b = next((x for x in base["proyectos"] if x["codigo"] == pr["codigo"]), None)
            if b:
                dur = (pd.Period(b["fin"], "M") - pd.Period(b["inicio"], "M")).n
                pr["fin"] = str(pd.Period(pr["inicio"], "M") + dur)
        proyectos.append(pr)
    equipo = entrada.get("equipo") or base["equipo"]
    cap = capacidad(equipo, base["feriados"], p, lista)
    a = asignar(proyectos, equipo, cap, p, lista)
    modo = "modelo"
    fin_hoja = {}
    from motor import db
    if db.disponible() and not entrada.get("modelo_automatico"):
        _asig_hoja = db.leer_asignaciones()
        a = desde_hoja(a, proyectos, equipo, cap, p, lista,
                       {"asig": _asig_hoja, "nec": db.leer_necesidades()})
        for x in _asig_hoja:   # último mes con días asignados en la Hoja = fin real del proyecto
            ms = [m for m, v in (x["dias"] or {}).items() if v and float(v) > 0]
            if ms:
                fin_hoja[x["codigo"]] = max(fin_hoja.get(x["codigo"], ""), max(ms))
        modo = "hoja"
    mc = montecarlo(proyectos, equipo, cap, p, lista) if con_mc else {"prob": {}, "dist_cubiertos": [], "n": 0, "media_factor": None}

    filas = []
    for pr in proyectos:
        r = a["resultado"][pr["codigo"]]
        asig = {pf: 0.0 for pf in PERFILES}
        for e_ in equipo:
            for d_ in a["asignacion"].get(e_["persona"], {}).values():
                asig[e_["perfil"]] += d_.get(pr["codigo"], 0)
        proy = r["req_hoja"] if "req_hoja" in r else {pf: round(float(sum(v)), 1) for pf, v in (r["req_mes"] or {}).items()}
        fin_real = str(pd.Period(pr["fin"], "M") + r["desplazo_meses"])
        fin_cons = fin_hoja.get(pr["codigo"]) or fin_real   # con asignaciones en la Hoja, el fin es su último mes con días
        dur = (pd.Period(fin_cons, "M") - (pd.Period(pr["inicio"], "M") + r["desplazo_meses"])).n + 1 if pr["codigo"] in fin_hoja \
            else (pd.Period(pr["fin"], "M") - pd.Period(pr["inicio"], "M")).n + 1
        opex_auto = sum(1 for m in lista if m > fin_cons) if p["opex"].get("inicia_mes_siguiente_al_fin", True) else 0
        om = pr.get("opex_meses")
        opex_meses = float(om) if om not in (None, "") and om == om else opex_auto
        filas.append({**{k: (pr.get(k) if pr.get(k) == pr.get(k) else "") or "" for k in ["pe", "oe_cod", "oe_desc", "programa", "responsable", "descripcion", "lider_ti"]},
                      **{k: pr[k] for k in ["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin",
                                              "capex", "opex_mensual", "incluir", "on_hold"]},
                      "inicio_plan": str(pd.Period(pr["inicio"], "M") + r["desplazo_meses"]), "fin_plan": fin_real, "fin_hoja": fin_cons,
                      "duracion_meses": dur, "recursos": {pf: float(pr.get(pf, 0) or 0) for pf in PERFILES},
                      "terceros": {t: float(pr.get("T " + t, 0) or 0) for t in TERCEROS},
                      "dias_terceros": float(sum(float(pr.get("T " + t, 0) or 0) for t in TERCEROS)),
                      "dias_base": float(sum(float(pr.get(pf, 0) or 0) for pf in PERFILES)),
                      "opex_anual": float(pr["opex_mensual"]) * 12, "capex_terceros": 0.0, "opex_horizonte": float(pr["opex_mensual"]) * opex_meses, "opex_meses": opex_meses, "opex_meses_auto": opex_auto, "opex_meses_manual": om not in (None, "") and om == om,
                      **{k: r[k] for k in ["estado", "cobertura", "desplazo_meses", "dias_requeridos"]},
                      "req_mes": r["req_mes"], "prob_cubierto": mc["prob"].get(pr["codigo"]),
                      "dias_proyectados": proy, "capacidad_asignada": {k: round(v, 1) for k, v in asig.items()}})

    # en modo hoja, las columnas de recursos muestran los días asignados en la Hoja de trabajo
    if modo == "hoja":
        tipo_de = {e["persona"]: e.get("tipo") or "interno" for e in equipo}
        perfil_de = {e["persona"]: e["perfil"] for e in equipo}
        a_tercero = {"Desarrollador": "Dev", "QA": "QA", "Analista Funcional": "Analista Funcional", "Analista Técnico": "Analista Técnico", "Seguridad TI": "Seguridad TI", "Devops": "Devops", "Soporte": "Soporte", "Demanda de Servicios": "Demanda de Servicios"}
        for f in filas:
            rec, ter = {pf: 0.0 for pf in PERFILES}, {t: 0.0 for t in TERCEROS}
            fuente = a.get("pausa", {}) if f.get("on_hold") else a["asignacion"]
            for n, ms in fuente.items():
                d = sum(x.get(f["codigo"], 0) for x in ms.values())
                if not d:
                    continue
                if tipo_de.get(n) == "tercero":
                    t = a_tercero.get(perfil_de[n])
                    if t:
                        ter[t] += d
                else:
                    rec[perfil_de[n]] += d
            f["recursos"], f["terceros"] = rec, ter
            f["dias_base"], f["dias_terceros"] = sum(rec.values()), sum(ter.values())
            # capex de terceros = días asignados ÷ días hábiles del mes × costo mensual del cargo
            costos = p.get("costos_terceros", {})
            cx = 0.0
            for n, ms in fuente.items():
                if tipo_de.get(n) != "tercero":
                    continue
                for mes, x in ms.items():
                    d = x.get(f["codigo"], 0)
                    c_mes = cap[n][lista.index(mes)] if mes in lista else 0
                    if d and c_mes:
                        cx += d / c_mes * float(costos.get(perfil_de[n], 0))
            f["capex_terceros"] = round(cx, 2)

    # capacidad vs. uso por perfil y mes
    gente = {pf: [e["persona"] for e in equipo if e["perfil"] == pf and e.get("tipo") != "tercero"] for pf in PERFILES}
    uso = []
    for pf in PERFILES:
        c = sum((cap[n] for n in gente[pf]), np.zeros(len(lista)))
        l = sum((a["libre"][n] for n in gente[pf]), np.zeros(len(lista)))
        uso.append({"perfil": pf, "personas": len(gente[pf]), "capacidad": c.round(1).tolist(),
                    "usado": (c - l).round(1).tolist()})
    # terceros: solo los cargos y meses en que se asignaron; % sobre la capacidad de los terceros asignados ese mes
    uso_ter = []
    for pf in PERFILES:
        ters = [e["persona"] for e in equipo if e["perfil"] == pf and e.get("tipo") == "tercero"]
        usados = [n for n in ters if a["asignacion"].get(n)]
        if not usados:
            continue
        dias, capm = [], []
        for i, mes in enumerate(lista):
            act = [n for n in usados if sum(a["asignacion"][n].get(mes, {}).values()) > 0]
            dias.append(round(sum(sum(a["asignacion"][n].get(mes, {}).values()) for n in act), 2))
            capm.append(round(float(sum(cap[n][i] for n in act)), 2))
        uso_ter.append({"perfil": pf, "personas": len(usados), "usado": dias, "capacidad": capm})
    personas = [{"persona": e["persona"], "perfil": e["perfil"], "genero": e.get("genero", ""),
                 "area": e.get("area") or "", "cargo": e.get("cargo") or "", "tipo": e.get("tipo") or "interno", "dias_semana": e.get("dias_semana", 5),
                 "capacidad": cap[e["persona"]].round(2).tolist(), "asignado": a["asignacion"].get(e["persona"], {})}
                for e in equipo]
    estados = {k: sum(1 for f in filas if f["estado"] == k) for k in ["cubierto", "parcial", "no_cubierto", "on_hold", "excluido"]}
    return {"meses": lista, "parametros": p, "perfiles": PERFILES, "terceros": TERCEROS, "proyectos": filas,
            "estados": estados, "uso_perfil": uso, "uso_terceros": uso_ter, "personas": personas, "montecarlo": mc, "origen": base.get("origen"), "modo": modo}


def base_inicio(base: dict, codigo: str) -> str | None:
    b = next((x for x in base["proyectos"] if x["codigo"] == codigo), None)
    return b["inicio"] if b else None
