"""Fichas referenciales (una sola vez): arma posiciones y asignaciones de proyectos supuestos, tamaño tipo Libélula,
usando la capacidad libre real del equipo propio y completando con terceros."""
from __future__ import annotations

import json

from motor import db

# rol: (días en el año, mes inicial, mes final) — meses 1..12 de 2027
PLANES = {
    "PRY-27-10": {   # Transformación de procesos de Supply
        "comentario": ("Ficha referencial (supuesto: estrategia de transformación de Supply ya definida e implementándose en 2027). "
                       "Fases: diagnóstico y diseño de procesos (Ene-Mar), construcción e integraciones con SAP/WMS (Mar-Oct), "
                       "pruebas y despliegue por oleadas (Jun-Dic), indicadores de abastecimiento y cierre (Jul-Dic). "
                       "Equipo: propio con capacidad libre y terceros donde no alcanza."),
        "roles": [("Gestor de TI", 60, 1, 12), ("Lider Tecnico", 60, 1, 12), ("Analista Funcional", 230, 1, 9),
                  ("Analista Técnico", 170, 2, 11), ("Desarrollador", 250, 3, 11), ("QA", 120, 5, 12),
                  ("Analytics engineer", 80, 7, 12), ("Experto en Data", 40, 7, 12)],
    },
    "PRY-27-16": {   # Transformación de procesos de Delivery
        "comentario": ("Ficha referencial (supuesto: estrategia de transformación de Delivery ya definida e implementándose en 2027). "
                       "Fases: diagnóstico del journey y rediseño de procesos (Ene-Mar), construcción e integración con agregadores, "
                       "OMS y última milla (Mar-Oct), pruebas y despliegue por marca (Jun-Dic), tablero de indicadores de delivery (Jul-Dic). "
                       "Equipo: propio con capacidad libre y terceros donde no alcanza."),
        "roles": [("Gestor de TI", 60, 1, 12), ("Lider Tecnico", 60, 1, 12), ("Analista Funcional", 220, 1, 9),
                  ("Analista Técnico", 160, 2, 11), ("Desarrollador", 250, 3, 11), ("QA", 120, 5, 12),
                  ("Devops", 40, 4, 12), ("Analytics engineer", 70, 7, 12)],
    },
}
MAX_PERSONAS_ROL = 2


def _r(x: float) -> float:
    return int(x * 2 + 1e-9) / 2   # medios días, redondeando hacia abajo (nunca pasa la capacidad)


def armar(salida: dict) -> list[str]:
    """salida = resultado de modelo.ejecutar(). Devuelve los proyectos armados."""
    meses = salida["meses"]
    personas = salida["personas"]
    libre = {}   # persona -> mes -> días libres
    for x in personas:
        libre[x["persona"]] = {m: max(0.0, float(x["capacidad"][i]) - sum((x["asignado"].get(m) or {}).values()))
                               for i, m in enumerate(meses)}
    armados = []
    with db._conn() as c:
        if c.execute("select 1 from gestor.meta where clave = 'fichas_ref_10_16_v1'").fetchone():
            return []
        for cod, plan in PLANES.items():
            ya = c.execute("select (select count(*) from gestor.asignaciones where codigo = %s) + (select count(*) from gestor.necesidades where codigo = %s)",
                           (cod, cod)).fetchone()[0]
            if ya:   # nunca pisar una ficha que alguien ya trabajó
                continue
            asign = {}   # persona -> {mes: días}
            for rol, dias, m1, m2 in plan["roles"]:
                rango = meses[m1 - 1:m2]
                c.execute("insert into gestor.necesidades (codigo, perfil, dias, mes_ini, mes_fin, dist) values (%s, %s, %s, %s, %s, null)",
                          (cod, rol, dias, rango[0], rango[-1]))
                pend = {m: dias / len(rango) for m in rango}
                internos = sorted([x for x in personas if x["perfil"] == rol and x.get("tipo") != "tercero"],
                                  key=lambda x: -sum(libre[x["persona"]][m] for m in rango))[:MAX_PERSONAS_ROL]
                terceros = sorted([x for x in personas if x["perfil"] == rol and x.get("tipo") == "tercero"],
                                  key=lambda x: -sum(libre[x["persona"]][m] for m in rango))
                for x in internos + terceros:
                    if sum(pend.values()) < 0.25:
                        break
                    d = {}
                    for m in rango:
                        t = _r(min(pend[m], libre[x["persona"]][m]))
                        if t > 0:
                            d[m] = t; pend[m] -= t; libre[x["persona"]][m] -= t
                    if d:
                        a = asign.setdefault(x["persona"], {})
                        for m, t in d.items():
                            a[m] = a.get(m, 0) + t
            for persona, d in asign.items():
                c.execute("insert into gestor.asignaciones (codigo, persona, dias) values (%s, %s, %s)", (cod, persona, json.dumps(d)))
            c.execute("""insert into gestor.observaciones (codigo, capex, opex, comentarios, actualizado) values (%s, '', '', %s, now())
                         on conflict (codigo) do update set comentarios = excluded.comentarios, actualizado = now()""", (cod, plan["comentario"]))
            r = c.execute("select data from gestor.escenario where id = 1").fetchone()
            esc = (r[0] if r else None) or {}
            esc[cod] = {**esc.get(cod, {}), "incluir": True, "on_hold": False, "inicio": meses[0]}
            c.execute("""insert into gestor.escenario (id, data, guardado) values (1, %s, now())
                         on conflict (id) do update set data = excluded.data, guardado = now()""", (json.dumps(esc),))
            tot = sum(sum(d.values()) for d in asign.values())
            print(f"fichas_ref: {cod} armada con {len(asign)} personas y {tot} días", json.dumps(asign, ensure_ascii=False), flush=True)
            armados.append(cod)
        c.execute("insert into gestor.meta values ('fichas_ref_10_16_v1', '1') on conflict do nothing")
    return armados
