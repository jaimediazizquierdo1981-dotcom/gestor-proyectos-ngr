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
DATOS_VERSION = "4"  # subir este número fuerza recargar los datos ficticios
PERFILES = ["PM", "Arquitecto", "Desarrollador", "Data Engineer", "QA", "Infra"]
COL = {"PM": "h_pm", "Arquitecto": "h_arquitecto", "Desarrollador": "h_desarrollador",
       "Data Engineer": "h_data_engineer", "QA": "h_qa", "Infra": "h_infra"}


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
  {", ".join(f"{c} numeric default 0" for c in COL.values())},
  actualizado timestamptz default now());
create table if not exists gestor.equipo (
  persona text primary key, perfil text not null, horas_semana numeric default 40, genero text);
drop table if exists gestor.horas_reales;
create table if not exists gestor.feriados (fecha date primary key, motivo text);
create table if not exists gestor.parametros (id int primary key, data jsonb not null, actualizado timestamptz default now());
create table if not exists gestor.meta (clave text primary key, valor text);
"""


def iniciar() -> None:
    if not disponible():
        return
    with _conn() as c:
        c.execute(DDL)
        v = c.execute("select valor from gestor.meta where clave = 'datos_version'").fetchone()
        if not v or v[0] != DATOS_VERSION:
            c.execute("truncate gestor.proyectos, gestor.equipo, gestor.feriados, gestor.parametros")
            sembrar(c)
            c.execute("insert into gestor.meta values ('datos_version', %s) on conflict (clave) do update set valor = excluded.valor",
                      (DATOS_VERSION,))


def sembrar(c) -> None:
    """Carga los datos ficticios de /data."""
    pr = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str})
    cols = ["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin", "capex", "opex_mensual"]
    with c.cursor() as cur:
        cur.executemany(
            f"insert into gestor.proyectos ({', '.join(cols + list(COL.values()))}) values ({', '.join(['%s'] * (len(cols) + 6))})",
            [[r[k] for k in cols] + [r[p] for p in PERFILES] for r in pr.to_dict(orient="records")])
        eq = pd.read_csv(DATA / "equipo.csv")
        cur.executemany("insert into gestor.equipo values (%s,%s,%s,%s) on conflict do nothing",
                        eq[["persona", "perfil", "horas_semana", "genero"]].values.tolist())
        fe = pd.read_csv(DATA / "feriados.csv")
        cur.executemany("insert into gestor.feriados values (%s,%s) on conflict do nothing", fe.values.tolist())
        cur.execute("insert into gestor.parametros (id, data) values (1, %s) on conflict (id) do nothing",
                    (json.dumps(json.loads((DATA / "parametros.json").read_text(encoding="utf-8"))),))


def leer() -> dict:
    with _conn() as c:
        cur = c.cursor()
        pr = cur.execute(f"select codigo, proyecto, tipo, marca, prioridad, inicio, fin, capex, opex_mensual, "
                         f"{', '.join(COL.values())} from gestor.proyectos order by prioridad").fetchall()
        proyectos = []
        for r in pr:
            d = dict(zip(["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin", "capex", "opex_mensual"], r[:9]))
            d.update({p: float(v or 0) for p, v in zip(PERFILES, r[9:])})
            for k in ["capex", "opex_mensual"]:
                d[k] = float(d[k] or 0)
            proyectos.append(d)
        equipo = [dict(zip(["persona", "perfil", "horas_semana", "genero"], (a, b, float(h), g)))
                  for a, b, h, g in cur.execute("select persona, perfil, horas_semana, genero from gestor.equipo order by perfil, persona")]
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
