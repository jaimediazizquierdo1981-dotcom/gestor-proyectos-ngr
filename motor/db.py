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
PERFILES = ["Gestor de TI", "Lider Tecnico", "Arquitecto", "Analista Funcional", "Analista Técnico", "Desarrollador", "Analytics engineer", "Data Engineer", "Practicante Data", "QA", "Infra", "Devops", "Seguridad TI", "Soporte", "Demanda de Servicios"]
COL = {"Gestor de TI": "d_pm", "Arquitecto": "d_arquitecto", "Analista Funcional": "d_analista_funcional",
       "Analista Técnico": "d_analista_tecnico", "Desarrollador": "d_desarrollador",
       "Data Engineer": "d_data_engineer", "QA": "d_qa", "Infra": "d_infra", "Seguridad TI": "d_seguridad_ti",
       "Lider Tecnico": "d_lider_tecnico", "Analytics engineer": "d_analytics_engineer", "Practicante Data": "d_practicante_data",
       "Devops": "d_devops", "Soporte": "d_soporte", "Demanda de Servicios": "d_demanda"}  # días por perfil interno
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
alter table gestor.proyectos add column if not exists pe text;
alter table gestor.proyectos add column if not exists d_lider_tecnico numeric default 0;
alter table gestor.proyectos add column if not exists d_analytics_engineer numeric default 0;
alter table gestor.proyectos add column if not exists d_practicante_data numeric default 0;
alter table gestor.proyectos add column if not exists d_devops numeric default 0;
alter table gestor.proyectos add column if not exists d_soporte numeric default 0;
alter table gestor.proyectos add column if not exists d_demanda numeric default 0;
alter table gestor.proyectos add column if not exists oe_cod text;
alter table gestor.proyectos add column if not exists oe_desc text;
alter table gestor.proyectos add column if not exists programa text;
alter table gestor.proyectos add column if not exists responsable text;
alter table gestor.proyectos add column if not exists descripcion text;
alter table gestor.proyectos add column if not exists lider_ti text;
alter table gestor.proyectos add column if not exists opex_meses numeric;
alter table gestor.proyectos add column if not exists orden int;
alter table gestor.proyectos alter column prioridad drop not null;
drop table if exists gestor.horas_reales;
create table if not exists gestor.feriados (fecha date primary key, motivo text);
create table if not exists gestor.parametros (id int primary key, data jsonb not null, actualizado timestamptz default now());
create table if not exists gestor.meta (clave text primary key, valor text);
create table if not exists gestor.necesidades (
  codigo text not null, perfil text not null, dias numeric not null default 0, primary key (codigo, perfil));
alter table gestor.necesidades add column if not exists mes_ini text;
alter table gestor.necesidades add column if not exists mes_fin text;
alter table gestor.necesidades add column if not exists dist jsonb;
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
        if not nueva:
            migrar_portafolio_2027(c)
            activar_portafolio_2027(c)
            remapear_codigos_viejos(c)
            cargar_colaboradores_npt(c)
        restaurar_libelula(c)
        if nueva:
            sembrar(c)
            c.execute("insert into gestor.meta values ('datos_version', %s) on conflict (clave) do update set valor = excluded.valor",
                      (DATOS_VERSION,))
            c.execute("insert into gestor.meta values ('portafolio_2027_v1', '1') on conflict do nothing")
        else:
            altas_equipo(c)
        portafolio_valido_2027(c)
        inicio_datos_maestros(c)
        opex_en_cero(c)
        montos_en_cero(c)
        cargar_lider_ti(c)
        # Nunca se borran aquí la hoja de trabajo (asignaciones, posiciones, observaciones) ni el escenario.


CAMPOS_PROY = ["codigo", "proyecto", "tipo", "marca", "prioridad", "inicio", "fin", "capex", "opex_mensual",
               "pe", "oe_cod", "oe_desc", "programa", "responsable", "descripcion", "lider_ti"]


def _insertar_proyectos(cur, pr) -> None:
    pr = pr.copy()
    for k in CAMPOS_PROY:
        if k not in pr:
            pr[k] = ""
    for p in list(COL) + list(TERC):
        if p not in pr:
            pr[p] = 0
    cur.executemany(
        f"insert into gestor.proyectos ({', '.join(CAMPOS_PROY + list(COL.values()) + list(TERC.values()))}) "
        f"values ({', '.join(['%s'] * (len(CAMPOS_PROY) + len(COL) + len(TERC)))})",
        [[(None if pd.isna(r[k]) else r[k]) for k in CAMPOS_PROY] + [r[p] for p in PERFILES] + [r[t] for t in TERC]
         for r in pr.to_dict(orient="records")])


def migrar_portafolio_2027(c) -> None:
    """Reemplaza los proyectos ficticios por el portafolio 2027 (una sola vez). Respalda la hoja antes."""
    if c.execute("select 1 from gestor.meta where clave = 'portafolio_2027_v1'").fetchone():
        return
    mapa = {"P01": "PRY-27-01", "P02": "PRY-27-06", "P03": "PRY-27-08", "P04": "PRY-27-03", "P06": "PRY-27-05"}
    c.execute("create table if not exists gestor.respaldo (id serial primary key, motivo text, tabla text, data jsonb, creado timestamptz default now())")
    for t in ("asignaciones", "necesidades", "observaciones", "escenario", "proyectos"):
        c.execute(f"insert into gestor.respaldo (motivo, tabla, data) select 'portafolio_2027_v1', '{t}', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from gestor.{t} x")
    opex = dict(c.execute("select codigo, opex_mensual from gestor.proyectos").fetchall())
    pr = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str, "oe_cod": str})
    for viejo, nuevo in mapa.items():   # conserva el opex que el usuario cargó
        if opex.get(viejo):
            pr.loc[pr.codigo == nuevo, "opex_mensual"] = float(opex[viejo])
    c.execute("delete from gestor.proyectos")
    with c.cursor() as cur:
        _insertar_proyectos(cur, pr)
    for t in ("asignaciones", "necesidades", "observaciones"):
        for viejo, nuevo in mapa.items():
            c.execute(f"update gestor.{t} set codigo = %s where codigo = %s", (nuevo, viejo))
        c.execute(f"delete from gestor.{t} where codigo not in (select codigo from gestor.proyectos)")
    r = c.execute("select data from gestor.escenario where id = 1").fetchone()
    if r:
        nueva = {}
        for viejo, v in (r[0] or {}).items():
            if viejo in mapa:
                nueva[mapa[viejo]] = {k: x for k, x in v.items() if k in ("incluir", "on_hold")}
        c.execute("update gestor.escenario set data = %s where id = 1", (json.dumps(nueva),))
    c.execute("insert into gestor.meta values ('portafolio_2027_v1', '1') on conflict do nothing")


MAPA_2027 = {"P01": "PRY-27-01", "P02": "PRY-27-06", "P03": "PRY-27-08", "P04": "PRY-27-03", "P06": "PRY-27-05"}


def remapear_codigos_viejos(c) -> None:
    """Si una pestaña antigua guardó la hoja con códigos P01…, se pasan a su iniciativa 2027 (y lo que quede huérfano se respalda).
    Si la iniciativa quedó vacía y hay respaldo de la migración, se recupera desde ahí."""
    for viejo, nuevo in MAPA_2027.items():
        c.execute("""update gestor.asignaciones a set codigo = %s where codigo = %s
                     and not exists (select 1 from gestor.asignaciones b where b.codigo = %s and b.persona = a.persona)""", (nuevo, viejo, nuevo))
        c.execute("""update gestor.necesidades a set codigo = %s where codigo = %s
                     and not exists (select 1 from gestor.necesidades b where b.codigo = %s and b.perfil = a.perfil)""", (nuevo, viejo, nuevo))
        c.execute("""update gestor.observaciones a set codigo = %s where codigo = %s
                     and not exists (select 1 from gestor.observaciones b where b.codigo = %s)""", (nuevo, viejo, nuevo))
    huer = c.execute("""select 'asignaciones', count(*) from gestor.asignaciones where codigo not in (select codigo from gestor.proyectos)
                        union all select 'necesidades', count(*) from gestor.necesidades where codigo not in (select codigo from gestor.proyectos)""").fetchall()
    if any(n for _, n in huer):
        for t in ("asignaciones", "necesidades", "observaciones"):
            c.execute(f"""insert into gestor.respaldo (motivo, tabla, data) select 'huerfanos', '{t}', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb)
                          from gestor.{t} x where codigo not in (select codigo from gestor.proyectos) having count(*) > 0""")
            c.execute(f"delete from gestor.{t} where codigo not in (select codigo from gestor.proyectos)")
    # recuperar (una sola vez) desde el respaldo de la migración lo que se haya perdido de las iniciativas mapeadas
    if c.execute("select 1 from gestor.meta where clave = 'recuperacion_2027_v1'").fetchone():
        return
    c.execute("insert into gestor.meta values ('recuperacion_2027_v1', '1') on conflict do nothing")
    for t, clave in (("asignaciones", "persona"), ("necesidades", "perfil")):
        r = c.execute("select data from gestor.respaldo where motivo = 'portafolio_2027_v1' and tabla = %s order by id limit 1", (t,)).fetchone()
        if not r:
            continue
        vacios = {n for n in MAPA_2027.values() if not c.execute(f"select 1 from gestor.{t} where codigo = %s limit 1", (n,)).fetchone()}
        for fila in r[0] or []:
            nuevo = MAPA_2027.get(fila.get("codigo"))
            if not nuevo or nuevo not in vacios:
                continue
            if t == "asignaciones":
                c.execute("insert into gestor.asignaciones (codigo, persona, dias) values (%s, %s, %s) on conflict do nothing",
                          (nuevo, fila["persona"], json.dumps(fila.get("dias") or {})))
            elif fila.get("perfil") != "_vacio":
                c.execute("insert into gestor.necesidades (codigo, perfil, dias, mes_ini, mes_fin, dist) values (%s,%s,%s,%s,%s,%s) on conflict do nothing",
                          (nuevo, fila["perfil"], fila.get("dias") or 0, fila.get("mes_ini"), fila.get("mes_fin"),
                           json.dumps(fila["dist"]) if fila.get("dist") else None))


def cargar_colaboradores_npt(c) -> None:
    """Una vez: reemplaza el equipo propio ficticio por el de la plantilla real (data/colaboradores_npt.xlsx)."""
    if c.execute("select 1 from gestor.meta where clave = 'colaboradores_npt_v1'").fetchone():
        return
    from motor import plantillas
    filas, _ = plantillas.leer((DATA / "colaboradores_npt.xlsx").read_bytes(), PERFILES)
    if not filas:
        return
    c.execute("insert into gestor.respaldo (motivo, tabla, data) select 'colaboradores_npt_v1', 'equipo', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from gestor.equipo x")
    c.execute("insert into gestor.respaldo (motivo, tabla, data) select 'colaboradores_npt_v1', 'asignaciones', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from gestor.asignaciones x")
    c.execute("delete from gestor.equipo where coalesce(tipo, 'interno') <> 'tercero'")
    with c.cursor() as cur:
        cur.executemany("""insert into gestor.equipo (persona, perfil, dias_semana, genero, area, cargo, tipo) values (%s,%s,%s,%s,%s,%s,'interno')
                           on conflict (persona) do update set perfil = excluded.perfil, dias_semana = excluded.dias_semana,
                           genero = excluded.genero, area = excluded.area, cargo = excluded.cargo, tipo = 'interno'""",
                        [(f["persona"], f["perfil"], f["dias_semana"], f["genero"], f["area"], f["cargo"]) for f in filas])
    c.execute("delete from gestor.asignaciones where persona not in (select persona from gestor.equipo)")
    c.execute("insert into gestor.meta values ('colaboradores_npt_v1', '1') on conflict do nothing")


def activar_portafolio_2027(c) -> None:
    """Una vez: las iniciativas del portafolio 2027 quedan todas incluidas (la migración arrastró exclusiones de los ficticios)."""
    if c.execute("select 1 from gestor.meta where clave = 'portafolio_2027_incluir'").fetchone():
        return
    r = c.execute("select data from gestor.escenario where id = 1").fetchone()
    if r and r[0]:
        data = {k: {kk: vv for kk, vv in v.items() if kk != "incluir"} for k, v in r[0].items()}
        c.execute("update gestor.escenario set data = %s where id = 1", (json.dumps(data),))
    c.execute("insert into gestor.meta values ('portafolio_2027_incluir', '1') on conflict do nothing")


def sembrar(c) -> None:
    """Carga los datos ficticios de /data."""
    pr = pd.read_csv(DATA / "proyectos.csv", dtype={"inicio": str, "fin": str, "oe_cod": str})
    with c.cursor() as cur:
        _insertar_proyectos(cur, pr)
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
        nb = len(CAMPOS_PROY)
        pr = cur.execute(f"select {', '.join(CAMPOS_PROY)}, "
                         f"{', '.join(list(COL.values()) + list(TERC.values()))} from gestor.proyectos order by prioridad nulls last, orden nulls last, codigo").fetchall()
        proyectos = []
        for r in pr:
            d = dict(zip(CAMPOS_PROY, r[:nb]))
            for k in ["pe", "oe_cod", "oe_desc", "programa", "responsable", "descripcion", "marca", "lider_ti"]:
                d[k] = d[k] or ""
            d.update({p: float(v or 0) for p, v in zip(PERFILES, r[nb:nb + len(PERFILES)])})
            d.update({t: float(v or 0) for t, v in zip(TERC, r[nb + len(PERFILES):])})
            for k in ["capex", "opex_mensual"]:
                d[k] = float(d[k] or 0)
            proyectos.append(d)
        om = dict(cur.execute("select codigo, opex_meses from gestor.proyectos where opex_meses is not null").fetchall())
        for d in proyectos:
            d["opex_meses"] = float(om[d["codigo"]]) if d["codigo"] in om else None
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
        for cod, pf, dias, ini, fin, dist in c.execute("select codigo, perfil, dias, mes_ini, mes_fin, dist from gestor.necesidades order by codigo, perfil"):
            out.setdefault(cod, [])
            if pf != "_vacio":
                out[cod].append({"perfil": pf, "dias": float(dias), "desde": ini, "hasta": fin, "dist": dist})
    return out


def _fila_nec(codigo: str, i: dict) -> tuple:
    dist = {m: float(v) for m, v in (i.get("dist") or {}).items() if v and float(v) > 0} or None
    return (codigo, str(i["perfil"]), float(i["dias"]), i.get("desde"), i.get("hasta"), json.dumps(dist) if dist else None)


def cargar_posiciones(por_proyecto: dict) -> None:
    """Reemplaza las posiciones de los proyectos que vienen en la plantilla."""
    import psycopg
    with psycopg.connect(URL) as c:
        with c.cursor() as cur:
            for cod, items in por_proyecto.items():
                cur.execute("delete from gestor.necesidades where codigo = %s", (cod,))
                cur.executemany("insert into gestor.necesidades (codigo, perfil, dias, mes_ini, mes_fin, dist) values (%s, %s, %s, %s, %s, %s)",
                                [_fila_nec(cod, i) for i in items] or [(cod, "_vacio", 0, None, None, None)])
        c.commit()


def guardar_necesidades(codigo: str, items: list[dict]) -> None:
    with _conn() as c:
        c.execute("delete from gestor.necesidades where codigo = %s", (codigo,))
        filas = [_fila_nec(codigo, i) for i in items if float(i.get("dias", 0)) > 0]
        if not filas:   # el usuario quitó todas: se recuerda que está vacío
            filas = [(codigo, "_vacio", 0, None, None, None)]
        with c.cursor() as cur:
            cur.executemany("insert into gestor.necesidades (codigo, perfil, dias, mes_ini, mes_fin, dist) values (%s, %s, %s, %s, %s, %s)", filas)


def actualizar_capex(codigo: str, capex: float) -> None:
    """Capex adicional del proyecto (equipos, asesorías, licencias…), aparte del Capex de terceros."""
    with _conn() as c:
        c.execute("update gestor.proyectos set capex = %s, actualizado = now() where codigo = %s", (capex, codigo))


def actualizar_proyecto(codigo: str, opex_mensual: float | None = None, opex_meses=False) -> None:
    with _conn() as c:
        if opex_meses is not False:   # None = automático (meses desde el fin hasta dic.)
            c.execute("update gestor.proyectos set opex_meses = %s, actualizado = now() where codigo = %s", (opex_meses, codigo))
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
    if c.execute("select 1 from gestor.meta where clave = 'restaurado_p01_v2'").fetchone():
        return
    if (c.execute("select 1 from gestor.proyectos where codigo = 'P01'").fetchone()
            and c.execute("select count(*) from gestor.asignaciones where codigo = 'P01'").fetchone()[0] == 0):
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
            cur.executemany("insert into gestor.necesidades (codigo, perfil, dias) values ('P01', %s, %s)",
                            [("Gestor de TI", 10), ("Arquitecto", 6), ("Analista Funcional", 11)])
    c.execute("insert into gestor.meta values ('restaurado_p01_v2', '1') on conflict do nothing")


# ---------- guardado completo de la hoja de trabajo (una sola transacción)
def leer_escenario() -> dict:
    with _conn() as c:
        r = c.execute("select data, guardado from gestor.escenario where id = 1").fetchone()
    return {"data": r[0], "guardado": r[1].isoformat()} if r else {"data": {}, "guardado": None}


def guardar_hoja(body: dict) -> str:
    import psycopg
    with psycopg.connect(URL) as c:   # transacción: o se guarda todo o nada
        with c.cursor() as cur:
            vigentes = {r[0] for r in cur.execute("select codigo from gestor.proyectos")}
            body = {**body, "asignaciones": [a for a in body.get("asignaciones", []) if MAPA_2027.get(a["codigo"], a["codigo"]) in vigentes],
                    "necesidades": {MAPA_2027.get(k, k): v for k, v in (body.get("necesidades") or {}).items() if MAPA_2027.get(k, k) in vigentes},
                    "observaciones": {MAPA_2027.get(k, k): v for k, v in (body.get("observaciones") or {}).items() if MAPA_2027.get(k, k) in vigentes}}
            for a in body["asignaciones"]:
                a["codigo"] = MAPA_2027.get(a["codigo"], a["codigo"])
            cur.execute("delete from gestor.asignaciones")
            cur.executemany("insert into gestor.asignaciones (codigo, persona, dias) values (%s, %s, %s)",
                            [(a["codigo"], a["persona"], json.dumps({m: float(v) for m, v in (a.get("dias") or {}).items() if v and float(v) > 0}))
                             for a in body.get("asignaciones", [])])
            cur.execute("delete from gestor.necesidades")
            filas = []
            for cod, items in (body.get("necesidades") or {}).items():
                its = [_fila_nec(cod, i) for i in items if float(i.get("dias", 0)) > 0]
                filas += its or [(cod, "_vacio", 0, None, None, None)]
            cur.executemany("insert into gestor.necesidades (codigo, perfil, dias, mes_ini, mes_fin, dist) values (%s, %s, %s, %s, %s, %s)", filas)
            cur.executemany("""insert into gestor.observaciones (codigo, capex, opex, actualizado) values (%s, %s, %s, now())
                               on conflict (codigo) do update set capex = excluded.capex, opex = excluded.opex, actualizado = now()""",
                            [(cod, o.get("capex", ""), o.get("opex", "")) for cod, o in (body.get("observaciones") or {}).items()])
            cur.executemany("update gestor.proyectos set opex_mensual = %s, actualizado = now() where codigo = %s",
                            [(float(v or 0), cod) for cod, v in (body.get("opex") or {}).items()])
            if "capex" in body:
                cur.executemany("update gestor.proyectos set capex = %s where codigo = %s",
                                [(float(v or 0), cod) for cod, v in (body.get("capex") or {}).items()])
            if "opex_meses" in body:
                cur.executemany("update gestor.proyectos set opex_meses = %s where codigo = %s",
                                [(None if v in (None, "") else float(v), cod) for cod, v in (body.get("opex_meses") or {}).items()])
            cur.execute("""insert into gestor.escenario (id, data, guardado) values (1, %s, now())
                           on conflict (id) do update set data = excluded.data, guardado = now() returning guardado""",
                        (json.dumps(body.get("escenario") or {}),))
            g = cur.fetchone()[0]
        c.commit()
    return g.isoformat()


def portafolio_valido_2027(c) -> None:
    """Una sola vez: actualiza las iniciativas con el portafolio 2027 validado (nombres, alineamiento, prioridad,
    responsable y Lider.TI). Respeta lo cargado por el usuario (capex, opex, hoja). Sin prioridad = vacía."""
    if c.execute("select 1 from gestor.meta where clave = 'portafolio_valido_2027_v1'").fetchone():
        return
    c.execute("create table if not exists gestor.respaldo (id serial primary key, motivo text, tabla text, data jsonb, creado timestamptz default now())")
    for t in ("proyectos", "escenario"):
        c.execute(f"insert into gestor.respaldo (motivo, tabla, data) select 'portafolio_valido_2027_v1', '{t}', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb) from gestor.{t} x")
    pr = pd.read_csv(DATA / "proyectos.csv", dtype=str).fillna("")
    existentes = {r[0] for r in c.execute("select codigo from gestor.proyectos").fetchall()}
    nuevos = pr[~pr.codigo.isin(existentes)]
    with c.cursor() as cur:
        if len(nuevos):
            n = nuevos.copy(); n["prioridad"] = n["prioridad"].replace("", None)
            for k in ("capex", "opex_mensual"): n[k] = n[k].replace("", "0").astype(float)
            _insertar_proyectos(cur, n)
        cur.executemany("""update gestor.proyectos set proyecto = %s, pe = %s, oe_cod = %s, oe_desc = %s, programa = %s, descripcion = %s,
                           prioridad = %s, responsable = %s, lider_ti = %s, orden = %s where codigo = %s""",
                        [(r["proyecto"], r["pe"], r["oe_cod"], r["oe_desc"], r["programa"], r["descripcion"],
                          int(float(r["prioridad"])) if r["prioridad"] else None, r["responsable"], r["lider_ti"], i, r["codigo"])
                         for i, r in enumerate(pr.to_dict(orient="records"))])
    # la prioridad la manda el archivo: se quita la que estaba guardada en el escenario
    r = c.execute("select data from gestor.escenario where id = 1").fetchone()
    if r and r[0]:
        c.execute("update gestor.escenario set data = %s where id = 1",
                  (json.dumps({k: {a: b for a, b in v.items() if a != "prioridad"} for k, v in r[0].items()}),))
    c.execute("insert into gestor.meta values ('portafolio_valido_2027_v1', '1') on conflict do nothing")


def inicio_datos_maestros(c) -> None:
    """Una sola vez: Datos maestros (PRY-27-08) vuelve a iniciar en Ene-27 como el resto (se quita el inicio guardado)."""
    if c.execute("select 1 from gestor.meta where clave = 'inicio_pry_27_08_v1'").fetchone():
        return
    r = c.execute("select data from gestor.escenario where id = 1").fetchone()
    esc = (r[0] or {}) if r else {}
    asig = c.execute("select persona, dias from gestor.asignaciones where codigo = 'PRY-27-08'").fetchall()
    nec = c.execute("select perfil, mes_ini, mes_fin from gestor.necesidades where codigo = 'PRY-27-08'").fetchall()
    print("PRY-27-08 diagnostico:", json.dumps({"escenario": esc.get("PRY-27-08"), "asig": [list(x) for x in asig], "nec": [list(x) for x in nec]}, default=str), flush=True)
    if "PRY-27-08" in esc:
        esc["PRY-27-08"] = {k: v for k, v in esc["PRY-27-08"].items() if k not in ("inicio", "fin")}
        c.execute("update gestor.escenario set data = %s where id = 1", (json.dumps(esc),))
    c.execute("update gestor.proyectos set inicio = '2027-01' where codigo = 'PRY-27-08'")
    c.execute("insert into gestor.meta values ('inicio_pry_27_08_v1', '1') on conflict do nothing")


def opex_en_cero(c) -> None:
    """Una sola vez: Opex mensual en 0 en todas las iniciativas salvo Libélula (PRY-27-01), que cargó el usuario.
    Arrastre de los proyectos de prueba. Se respalda antes."""
    if c.execute("select 1 from gestor.meta where clave = 'opex_cero_v1'").fetchone():
        return
    c.execute("create table if not exists gestor.respaldo (id serial primary key, motivo text, tabla text, data jsonb, creado timestamptz default now())")
    c.execute("""insert into gestor.respaldo (motivo, tabla, data) select 'opex_cero_v1', 'proyectos',
                 coalesce(jsonb_agg(jsonb_build_object('codigo', codigo, 'opex_mensual', opex_mensual, 'opex_meses', opex_meses)), '[]'::jsonb) from gestor.proyectos""")
    antes = c.execute("select codigo, opex_mensual from gestor.proyectos where codigo <> 'PRY-27-01' and coalesce(opex_mensual, 0) <> 0").fetchall()
    print("opex_en_cero:", [(a, float(b)) for a, b in antes], flush=True)
    c.execute("update gestor.proyectos set opex_mensual = 0 where codigo <> 'PRY-27-01'")
    c.execute("insert into gestor.meta values ('opex_cero_v1', '1') on conflict do nothing")


def montos_en_cero(c) -> None:
    """Una sola vez (pedido del usuario): todos los montos en 0 (Capex adicional, Opex mensual y meses de Opex). Se respalda antes."""
    if c.execute("select 1 from gestor.meta where clave = 'montos_cero_v1'").fetchone():
        return
    c.execute("""insert into gestor.respaldo (motivo, tabla, data) select 'montos_cero_v1', 'proyectos',
                 coalesce(jsonb_agg(jsonb_build_object('codigo', codigo, 'capex', capex, 'opex_mensual', opex_mensual, 'opex_meses', opex_meses)), '[]'::jsonb) from gestor.proyectos""")
    c.execute("update gestor.proyectos set capex = 0, opex_mensual = 0, opex_meses = null")
    c.execute("insert into gestor.meta values ('montos_cero_v1', '1') on conflict do nothing")


def cargar_lider_ti(c) -> None:
    """Completa la columna Lider.TI desde el CSV del portafolio (solo donde está vacía)."""
    pr = pd.read_csv(DATA / "proyectos.csv")
    with c.cursor() as cur:
        cur.executemany("update gestor.proyectos set lider_ti = %s where codigo = %s and coalesce(lider_ti, '') = ''",
                        [(str(l), cod) for cod, l in zip(pr["codigo"], pr["lider_ti"].fillna("")) if l])


def altas_equipo(c) -> None:
    """Agrega las personas nuevas del CSV (p. ej. un rol nuevo) sin tocar a las existentes."""
    eq = pd.read_csv(DATA / "equipo.csv")
    with c.cursor() as cur:
        # rol renombrado: Demanda -> Demanda de Servicios (personas y posiciones de la hoja)
        for viejo, nuevo in [("PM", "Gestor de TI"), ("Orquestador de TI", "Gestor de TI")]:
            cur.execute("update gestor.equipo set perfil = %s where perfil = %s", (nuevo, viejo))
            cur.execute("""update gestor.necesidades n set perfil = %s where perfil = %s and not exists
                           (select 1 from gestor.necesidades m where m.codigo = n.codigo and m.perfil = %s)""", (nuevo, viejo, nuevo))
        cur.execute("update gestor.equipo set perfil = 'Demanda de Servicios' where perfil = 'Demanda'")
        cur.execute("""update gestor.necesidades n set perfil = 'Demanda de Servicios' where perfil = 'Demanda'
                       and not exists (select 1 from gestor.necesidades m where m.codigo = n.codigo and m.perfil = 'Demanda de Servicios')""")
        cur.executemany("insert into gestor.equipo (persona, perfil, dias_semana, genero, area, cargo, tipo) values (%s,%s,%s,%s,%s,%s,%s) on conflict do nothing",
                        eq[["persona", "perfil", "dias_semana", "genero", "area", "cargo", "tipo"]].fillna("").values.tolist())


def cargar_colaboradores(filas: list[dict], modo: str) -> dict:
    """Actualiza/inserta colaboradores propios; en modo 'reemplazar' quita a los internos que no vengan en el archivo."""
    import psycopg
    with psycopg.connect(URL) as c:
        with c.cursor() as cur:
            antes = {r[0] for r in cur.execute("select persona from gestor.equipo where coalesce(tipo,'interno') <> 'tercero'")}
            for f in filas:
                cur.execute("""insert into gestor.equipo (persona, perfil, dias_semana, genero, area, cargo, tipo)
                               values (%s,%s,%s,%s,%s,%s,'interno')
                               on conflict (persona) do update set perfil = excluded.perfil, dias_semana = excluded.dias_semana,
                               genero = excluded.genero, area = excluded.area, cargo = excluded.cargo""",
                            (f["persona"], f["perfil"], f["dias_semana"], f["genero"], f["area"], f["cargo"]))
            quitados = []
            if modo == "reemplazar":
                quitados = sorted(antes - {f["persona"] for f in filas})
                if quitados:
                    cur.execute("delete from gestor.equipo where persona = any(%s) and coalesce(tipo,'interno') <> 'tercero'", (quitados,))
        c.commit()
    nombres = {f["persona"] for f in filas}
    return {"nuevos": sorted(nombres - antes), "actualizados": sorted(nombres & antes), "quitados": quitados}


def eliminar_ficha(codigo: str) -> None:
    """Borra la ficha del proyecto en la hoja (asignaciones, posiciones, observaciones) y lo saca del escenario."""
    import psycopg
    with psycopg.connect(URL) as c:
        with c.cursor() as cur:
            for t in ("asignaciones", "necesidades", "observaciones"):
                cur.execute(f"delete from gestor.{t} where codigo = %s", (codigo,))
            # también sus montos: Capex adicional, Opex mensual y meses de Opex
            cur.execute("update gestor.proyectos set capex = 0, opex_mensual = 0, opex_meses = null where codigo = %s", (codigo,))
            r = cur.execute("select data from gestor.escenario where id = 1").fetchone()
            data = r[0] if r else {}
            data[codigo] = {k: v for k, v in data.get(codigo, {}).items() if k not in ("inicio", "fin")}
            data[codigo].update({"incluir": False, "on_hold": False})
            cur.execute("""insert into gestor.escenario (id, data) values (1, %s)
                           on conflict (id) do update set data = excluded.data""", (json.dumps(data),))
        c.commit()
