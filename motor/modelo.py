"""Motor del gestor de proyectos.

1. Capacidad neta por persona y mes (días hábiles - feriados - vacaciones - reserva para operación).
2. Distribución del esfuerzo de cada proyecto en el tiempo (uniforme o curva de Rayleigh / Norden-Putnam).
3. Asignación con recursos limitados por prioridad (RCPSP, esquema serial con regla de prioridad).
4. Riesgo: Monte Carlo con esfuerzo PERT-Beta -> probabilidad de que cada proyecto quede cubierto.
5. Horas proyectadas vs. capacidad real que el equipo le puede asignar a cada proyecto.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
PERFILES = ["PM", "Arquitecto", "Desarrollador", "Data Engineer", "QA", "Infra"]


# ---------------------------------------------------------------- utilidades
def _fusionar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _fusionar(out[k], v)
        else:
            out[k] = v
    return out


def meses(inicio: str, fin: str) -> list[str]:
    return [str(p) for p in pd.period_range(inicio, fin, freq="M")]


def _idx(m: str, lista: list[str]) -> int:
    return lista.index(m) if m in lista else (-1 if m < lista[0] else len(lista))


def cargar_base() -> dict:
    from motor import db
    if db.disponible():
        b = db.leer()
        if b["parametros"] is None:
            b["parametros"] = json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))
        b["perfiles"] = PERFILES
        b["origen"] = "base de datos"
        return b
    params = json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))
    proyectos = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str}).fillna(0)
    equipo = pd.read_csv(DATA / "equipo.csv")
    feriados = pd.read_csv(DATA / "feriados.csv")
    return {"parametros": params, "proyectos": proyectos.to_dict(orient="records"),
            "equipo": equipo.to_dict(orient="records"), "feriados": feriados.to_dict(orient="records"),
            "perfiles": PERFILES, "origen": "archivos"}


# ---------------------------------------------------------------- 1. capacidad
def capacidad(equipo: list[dict], feriados: list[dict], p: dict, lista: list[str]) -> dict:
    """Horas netas disponibles para proyectos, por persona y mes."""
    c = p["capacidad"]
    fer = {pd.Timestamp(f["fecha"]).date() for f in feriados} if c.get("descontar_feriados", True) else set()
    out = {}
    for e in equipo:
        fila = []
        hd = float(c["horas_por_dia"]) * float(e.get("horas_semana", 40) or 40) / 40
        for m in lista:
            per = pd.Period(m, "M")
            dias = pd.bdate_range(per.start_time, per.end_time)
            habiles = sum(1 for d in dias if d.date() not in fer)
            vac = float(c.get("dias_vacaciones_anual", 0)) / 12
            bruto = max(habiles - vac, 0) * hd
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
    """Horas requeridas por perfil y mes (vector sobre el horizonte)."""
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
    por_perfil = {pf: [e["persona"] for e in equipo if e["perfil"] == pf] for pf in PERFILES}
    activos = sorted([x for x in proyectos if x.get("incluir", True)], key=lambda x: float(x.get("prioridad", 99)))
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
                        if detalle and h > 0.05:
                            asign.setdefault(n, {}).setdefault(lista[i], {})[pr["codigo"]] = round(float(h), 1)
        res[pr["codigo"]] = {"estado": estado, "cobertura": round(float(ratio), 4), "desplazo_meses": d,
                             "horas_requeridas": round(float(tot), 1),
                             "req_mes": {pf: v.round(1).tolist() for pf, v in req.items()} if detalle else None}
    for pr in proyectos:
        if not pr.get("incluir", True):
            res[pr["codigo"]] = {"estado": "excluido", "cobertura": 0, "desplazo_meses": 0,
                                 "horas_requeridas": 0, "req_mes": None}
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
    act = [x for x in proyectos if x.get("incluir", True)]
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


# ---------------------------------------------------------------- orquestador
def ejecutar(entrada: dict | None = None, con_mc: bool = True) -> dict:
    entrada = entrada or {}
    base = cargar_base()
    p = _fusionar(base["parametros"], entrada.get("parametros", {}))
    lista = meses(p["horizonte"]["inicio"], p["horizonte"]["fin"])
    escenario = entrada.get("escenario", {})  # {codigo: {incluir, inicio, fin, prioridad}}
    proyectos = []
    for pr in entrada.get("proyectos") or base["proyectos"]:
        pr = {**pr, **escenario.get(pr["codigo"], {})}
        pr.setdefault("incluir", True)
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
    mc = montecarlo(proyectos, equipo, cap, p, lista) if con_mc else {"prob": {}, "dist_cubiertos": [], "n": 0, "media_factor": None}

    filas = []
    for pr in proyectos:
        r = a["resultado"][pr["codigo"]]
        asig = {pf: 0.0 for pf in PERFILES}
        for e_ in equipo:
            for d_ in a["asignacion"].get(e_["persona"], {}).values():
                asig[e_["perfil"]] += d_.get(pr["codigo"], 0)
        proy = {pf: round(float(sum(v)), 1) for pf, v in (r["req_mes"] or {}).items()}
        fin_real = str(pd.Period(pr["fin"], "M") + r["desplazo_meses"])
        dur = (pd.Period(pr["fin"], "M") - pd.Period(pr["inicio"], "M")).n + 1
        opex_meses = sum(1 for m in lista if m > fin_real) if p["opex"].get("inicia_mes_siguiente_al_fin", True) else 0
        filas.append({**{k: pr[k] for k in ["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin",
                                              "capex", "opex_mensual", "incluir"]},
                      "inicio_plan": str(pd.Period(pr["inicio"], "M") + r["desplazo_meses"]), "fin_plan": fin_real,
                      "duracion_meses": dur, "recursos": {pf: float(pr.get(pf, 0) or 0) for pf in PERFILES},
                      "horas_base": float(sum(float(pr.get(pf, 0) or 0) for pf in PERFILES)),
                      "opex_anual": float(pr["opex_mensual"]) * 12, "opex_horizonte": float(pr["opex_mensual"]) * opex_meses,
                      **{k: r[k] for k in ["estado", "cobertura", "desplazo_meses", "horas_requeridas"]},
                      "req_mes": r["req_mes"], "prob_cubierto": mc["prob"].get(pr["codigo"]),
                      "horas_proyectadas": proy, "capacidad_asignada": {k: round(v, 1) for k, v in asig.items()}})

    # capacidad vs. uso por perfil y mes
    gente = {pf: [e["persona"] for e in equipo if e["perfil"] == pf] for pf in PERFILES}
    uso = []
    for pf in PERFILES:
        c = sum((cap[n] for n in gente[pf]), np.zeros(len(lista)))
        l = sum((a["libre"][n] for n in gente[pf]), np.zeros(len(lista)))
        uso.append({"perfil": pf, "personas": len(gente[pf]), "capacidad": c.round(1).tolist(),
                    "usado": (c - l).round(1).tolist()})
    personas = [{"persona": e["persona"], "perfil": e["perfil"], "genero": e.get("genero", ""),
                 "area": e.get("area") or "", "cargo": e.get("cargo") or "", "horas_semana": e.get("horas_semana", 40),
                 "capacidad": cap[e["persona"]].round(1).tolist(), "asignado": a["asignacion"].get(e["persona"], {})}
                for e in equipo]
    estados = {k: sum(1 for f in filas if f["estado"] == k) for k in ["cubierto", "parcial", "no_cubierto", "excluido"]}
    return {"meses": lista, "parametros": p, "perfiles": PERFILES, "proyectos": filas,
            "estados": estados, "uso_perfil": uso, "personas": personas, "montecarlo": mc, "origen": base.get("origen")}


def base_inicio(base: dict, codigo: str) -> str | None:
    b = next((x for x in base["proyectos"] if x["codigo"] == codigo), None)
    return b["inicio"] if b else None
