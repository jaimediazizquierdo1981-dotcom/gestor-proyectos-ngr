"""Base de datos PostgreSQL en Render (esquema `gestor`).

Si existe DATABASE_URL, los datos se leen de la base. Al arrancar se crean las tablas y,
si están vacías, se cargan con los datos ficticios de /data. Sin DATABASE_URL se usan los CSV.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

URL = os.environ.get("DATABASE_URL", "").strip()
DATA = Path(__file__).resolve().parent.parent / "data"
DATOS_VERSION = "12"  # subir este número fuerza recargar los datos ficticios
PERFILES = ["PM", "Arquitecto", "Analista Funcional", "Analista Técnico", "Desarrollador", "Data Engineer", "QA", "Infra", "Seguridad TI"]
COL = {"PM": "d_pm", "Arquitecto": "d_arquitecto", "Analista Funcional": "d_analista_funcional",
       "Analista Técnico": "d_analista_tecnico", "Desarrollador": "d_desarrollador",
       "Data Engineer": "d_data_engineer", "QA": "d_qa", "Infra": "d_infra", "Seguridad TI": "d_seguridad_ti"}  # días por perfil interno
TERC = {"T Dev": "t_dev", "T QA": "t_qa", "T Analista Funcional": "t_analista_funcional",
        "T Analista Técnico": "t_analista_tecnico"}  # días de terceros


def disponible() -> bool:
    return bool(URL)


@contextmanager
def _conn():
    import psycopg
    with psycopg.connect(URL, autocommit=True) as c:
        yield c


DDL = f"""
create schema if not exists gestor;
create table if not exists gestor.proyectos (
  codigo text primary key, proyecto text not null, tipo text not null, marca text,
  prioridad int not null, inicio text not null, fin text not null,
  capex numeric default 0, opex_mensual numeric default 0,
  {", ".join(f"{c} numeric default 0" for c in list(COL.values()) + list(TERC.values()))},
  actualizado timestamptz default now());
create table if not exists gestor.equipo (
  persona text primary key, perfil text not null, dias_semana numeric default 5, genero text);
alter table gestor.equipo add column if not exists area text;
alter table gestor.equipo add column if not exists cargo text;
alter table gestor.equipo add column if not exists tipo text default 'interno';
alter table gestor.proyectos add column if not exists d_seguridad_ti numeric default 0;
drop table if exists gestor.horas_reales;
create table if not exists gestor.feriados (fecha date primary key, motivo text);
create table if not exists gestor.parametros (id int primary key, data jsonb not null, actualizado timestamptz default now());
create table if not exists gestor.meta (clave text primary key, valor text);
create table if not exists gestor.necesidades (
  codigo text not null, perfil text not null, dias numeric not null default 0, primary key (codigo, perfil));
create table if not exists gestor.observaciones (
  codigo text primary key, capex text default '', opex text default '', actualizado timestamptz default now());
create table if not exists gestor.escenario (
  id int primary key, data jsonb not null default '{{}}'::jsonb, guardado timestamptz default now());
create table if not exists gestor.asignaciones (
  codigo text not null, persona text not null, dias jsonb not null default '{{}}'::jsonb,
  actualizado timestamptz default now(), primary key (codigo, persona));
"""


def iniciar() -> None:
    if not disponible():
        return
    with _conn() as c:
        c.execute("create schema if not exists gestor; create table if not exists gestor.meta (clave text primary key, valor text);")
        v = c.execute("select valor from gestor.meta where clave = 'datos_version'").fetchone()
        nueva = not v or v[0] != DATOS_VERSION
        if nueva:   # cambia la estructura: se recrean las tablas y se recargan los datos ficticios
            c.execute("drop table if exists gestor.proyectos, gestor.equipo, gestor.feriados, gestor.parametros, gestor.horas_reales cascade")
        c.execute(DDL)
        restaurar_libelula(c)
        if nueva:
            sembrar(c)
        else:
            altas_equipo(c)
            if DATOS_VERSION == "12":   # arranque limpio pedido: sin posiciones ni asignaciones de prueba en la hoja
                c.execute("delete from gestor.necesidades; delete from gestor.asignaciones; delete from gestor.observaciones;")
            c.execute("insert into gestor.meta values ('datos_version', %s) on conflict (clave) do update set valor = excluded.valor",
                      (DATOS_VERSION,))


def sembrar(c) -> None:
    """Carga los datos ficticios de /data."""
    pr = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str})
    cols = ["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin", "capex", "opex_mensual"]
    with c.cursor() as cur:
        cur.executemany(
            f"insert into gestor.proyectos ({', '.join(cols + list(COL.values()) + list(TERC.values()))}) "
            f"values ({', '.join(['%s'] * (len(cols) + len(COL) + len(TERC)))})",
            [[r[k] for k in cols] + [r[p] for p in PERFILES] + [r[t] for t in TERC] for r in pr.to_dict(orient="records")])
        eq = pd.read_csv(DATA / "equipo.csv")
        cur.executemany("insert into gestor.equipo (persona, perfil, dias_semana, genero, area, cargo, tipo) values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                        eq[["persona", "perfil", "dias_semana", "genero", "area", "cargo", "tipo"]].fillna("").values.tolist())
        fe = pd.read_csv(DATA / "feriados.csv")
        cur.executemany("insert into gestor.feriados values (%s,%s) on conflict do nothing", fe.values.tolist())
        cur.execute("insert into gestor.parametros (id, data) values (1, %s) on conflict (id) do nothing",
                    (json.dumps(json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))),))


def leer() -> dict:
    with _conn() as c:
        cur = c.cursor()
        pr = cur.execute(f"select codigo, proyecto, tipo, marca, prioridad, inicio, fin, capex, opex_mensual, "
                         f"{', '.join(list(COL.values()) + list(TERC.values()))} from gestor.proyectos order by prioridad").fetchall()
        proyectos = []
        for r in pr:
            d = dict(zip(["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin", "capex", "opex_mensual"], r[:9]))
            d.update({p: float(v or 0) for p, v in zip(PERFILES, r[9:9 + len(PERFILES)])})
            d.update({t: float(v or 0) for t, v in zip(TERC, r[9 + len(PERFILES):])})
            for k in ["capex", "opex_mensual"]:
                d[k] = float(d[k] or 0)
            proyectos.append(d)
        equipo = [{"persona": a, "perfil": b, "dias_semana": float(h), "genero": g, "area": ar, "cargo": cg, "tipo": t or "interno"}
                  for a, b, h, g, ar, cg, t in cur.execute("select persona, perfil, dias_semana, genero, area, cargo, tipo from gestor.equipo order by area, persona")]
        feriados = [{"fecha": str(f), "motivo": m} for f, m in cur.execute("select fecha, motivo from gestor.feriados order by fecha")]
        par = cur.execute("select data from gestor.parametros where id = 1").fetchone()
    return {"proyectos": proyectos, "equipo": equipo, "feriados": feriados,
            "parametros": par[0] if par else None}


def restaurar() -> None:
    """Borra todo y vuelve a cargar los datos ficticios."""
    with _conn() as c:
        c.execute("truncate gestor.proyectos, gestor.equipo, gestor.feriados, gestor.parametros")
        sembrar(c)


def estado() -> dict:
    if not disponible():
        return {"base_de_datos": False}
    with _conn() as c:
        n = {t: c.execute(f"select count(*) from gestor.{t}").fetchone()[0] for t in ["proyectos", "equipo", "feriados"]}
    return {"base_de_datos": True, "filas": n}


def actualizar_colaborador(persona: str, perfil: str | None = None, dias_semana: float | None = None) -> bool:
    with _conn() as c:
        if perfil is not None:
            c.execute("update gestor.equipo set perfil = %s where persona = %s", (perfil, persona))
        if dias_semana is not None:
            c.execute("update gestor.equipo set dias_semana = %s where persona = %s", (dias_semana, persona))
        return c.execute("select 1 from gestor.equipo where persona = %s", (persona,)).fetchone() is not None


# ---------- hoja de trabajo: asignaciones manuales persona-proyecto-mes (en días)
def leer_asignaciones() -> list[dict]:
    with _conn() as c:
        return [{"codigo": a, "persona": b, "dias": d} for a, b, d in
                c.execute("select codigo, persona, dias from gestor.asignaciones order by codigo, persona")]


def guardar_asignacion(codigo: str, persona: str, dias: dict) -> None:
    dias = {m: round(float(v), 2) for m, v in (dias or {}).items() if v and float(v) > 0}
    with _conn() as c:
        c.execute("""insert into gestor.asignaciones (codigo, persona, dias, actualizado) values (%s, %s, %s, now())
                     on conflict (codigo, persona) do update set dias = excluded.dias, actualizado = now()""",
                  (codigo, persona, json.dumps(dias)))


def borrar_asignacion(codigo: str, persona: str) -> None:
    with _conn() as c:
        c.execute("delete from gestor.asignaciones where codigo = %s and persona = %s", (codigo, persona))


# ---------- hoja de trabajo: posiciones que necesita cada proyecto (días), definidas por el usuario
def leer_necesidades() -> dict:
    out = {}
    with _conn() as c:
        for cod, pf, dias in c.execute("select codigo, perfil, dias from gestor.necesidades order by codigo, perfil"):
            out.setdefault(cod, [])
            if pf != "_vacio":
                out[cod].append({"perfil": pf, "dias": float(dias)})
    return out


def guardar_necesidades(codigo: str, items: list[dict]) -> None:
    with _conn() as c:
        c.execute("delete from gestor.necesidades where codigo = %s", (codigo,))
        filas = [(codigo, str(i["perfil"]), float(i["dias"])) for i in items if float(i.get("dias", 0)) > 0]
        if not filas:   # el usuario quitó todas: se recuerda que está vacío
            filas = [(codigo, "_vacio", 0)]
        with c.cursor() as cur:
            cur.executemany("insert into gestor.necesidades values (%s, %s, %s)", filas)


def actualizar_proyecto(codigo: str, opex_mensual: float | None = None) -> None:
    with _conn() as c:
        if opex_mensual is not None:
            c.execute("update gestor.proyectos set opex_mensual = %s, actualizado = now() where codigo = %s", (opex_mensual, codigo))


# ---------- observaciones de capex y opex por proyecto
def leer_observaciones() -> dict:
    with _conn() as c:
        return {cod: {"capex": cx or "", "opex": op or ""} for cod, cx, op in
                c.execute("select codigo, capex, opex from gestor.observaciones")}


def guardar_observacion(codigo: str, campo: str, texto: str) -> None:
    if campo not in ("capex", "opex"):
        raise ValueError("campo")
    with _conn() as c:
        c.execute(f"""insert into gestor.observaciones (codigo, {campo}, actualizado) values (%s, %s, now())
                      on conflict (codigo) do update set {campo} = excluded.{campo}, actualizado = now()""", (codigo, texto))


def restaurar_libelula(c) -> None:
    """Restaura una sola vez lo que el usuario había cargado en P01 (borrado por error en la versión 12)."""
    if c.execute("select 1 from gestor.meta where clave = 'restaurado_p01'").fetchone():
        return
    if c.execute("select count(*) from gestor.asignaciones where codigo = 'P01'").fetchone()[0] == 0:
        meses = [f"2027-{m:02d}" for m in range(1, 10)]
        filas = {
            "Carlos Vega": dict(zip(meses, [1, 1, 1, 1, 1, 1, 1, 1, 2])),
            "Héctor Ruiz": dict(zip(meses, [0.5] * 9)),
            "Lucía Ramos": dict(zip(meses, [0.5] * 8 + [2])),
            "Mariana Vidal": dict(zip(meses, [0.5, 0.5, 0.5, 0.5, 1, 1, 1, 1, 0.5])),
        }
        with c.cursor() as cur:
            cur.executemany("insert into gestor.asignaciones (codigo, persona, dias) values ('P01', %s, %s) on conflict do nothing",
                            [(n, json.dumps(v)) for n, v in filas.items()])
        c.execute("delete from gestor.necesidades where codigo = 'P01'")
        with c.cursor() as cur:
            cur.executemany("insert into gestor.necesidades values ('P01', %s, %s)",
                            [("PM", 10), ("Arquitecto", 6), ("Analista Funcional", 11)])
    c.execute("insert into gestor.meta values ('restaurado_p01', '1') on conflict do nothing")


# ---------- guardado completo de la hoja de trabajo (una sola transacción)
def leer_escenario() -> dict:
    with _conn() as c:
        r = c.execute("select data, guardado from gestor.escenario where id = 1").fetchone()
    return {"data": r[0], "guardado": r[1].isoformat()} if r else {"data": {}, "guardado": None}


def guardar_hoja(body: dict) -> str:
    import psycopg
    with psycopg.connect(URL) as c:   # transacción: o se guarda todo o nada
        with c.cursor() as cur:
            cur.execute("delete from gestor.asignaciones")
            cur.executemany("insert into gestor.asignaciones (codigo, persona, dias) values (%s, %s, %s)",
                            [(a["codigo"], a["persona"], json.dumps({m: float(v) for m, v in (a.get("dias") or {}).items() if v and float(v) > 0}))
                             for a in body.get("asignaciones", [])])
            cur.execute("delete from gestor.necesidades")
            filas = []
            for cod, items in (body.get("necesidades") or {}).items():
                its = [(cod, i["perfil"], float(i["dias"])) for i in items if float(i.get("dias", 0)) > 0]
                filas += its or [(cod, "_vacio", 0)]
            cur.executemany("insert into gestor.necesidades values (%s, %s, %s)", filas)
            cur.executemany("""insert into gestor.observaciones (codigo, capex, opex, actualizado) values (%s, %s, %s, now())
                               on conflict (codigo) do update set capex = excluded.capex, opex = excluded.opex, actualizado = now()""",
                            [(cod, o.get("capex", ""), o.get("opex", "")) for cod, o in (body.get("observaciones") or {}).items()])
            cur.executemany("update gestor.proyectos set opex_mensual = %s, actualizado = now() where codigo = %s",
                            [(float(v or 0), cod) for cod, v in (body.get("opex") or {}).items()])
            cur.execute("""insert into gestor.escenario (id, data, guardado) values (1, %s, now())
                           on conflict (id) do update set data = excluded.data, guardado = now() returning guardado""",
                        (json.dumps(body.get("escenario") or {}),))
            g = cur.fetchone()[0]
        c.commit()
    return g.isoformat()


def altas_equipo(c) -> None:
    """Agrega las personas nuevas del CSV (p. ej. un rol nuevo) sin tocar a las existentes."""
    eq = pd.read_csv(DATA / "equipo.csv")
    with c.cursor() as cur:
        cur.executemany("insert into gestor.equipo (persona, perfil, dias_semana, genero, area, cargo, tipo) values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                        eq[["persona", "perfil", "dias_semana", "genero", "area", "cargo", "tipo"]].fillna("").values.tolist())
