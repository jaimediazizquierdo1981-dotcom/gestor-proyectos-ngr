"""Equilibrio de terceros: pasa proyectos completos de un tercero con exceso a otro del mismo rol con días libres
(mismo criterio que la propuesta de la pestaña Tops). Se ejecuta una sola vez por clave y respalda antes."""
import json


def proponer(R: dict) -> list[dict]:
    M = R["meses"]
    ter = [p for p in R["personas"] if p.get("tipo") == "tercero"]
    uso = {p["persona"]: [sum((p["asignado"].get(m) or {}).values()) for m in M] for p in ter}
    asig = {p["persona"]: {m: dict(p["asignado"].get(m) or {}) for m in M} for p in ter}
    cap = {p["persona"]: [float(x) for x in p["capacidad"]] for p in ter}
    exceso = lambda n: sum(max(0.0, uso[n][i] - cap[n][i]) for i in range(len(M)))
    movs = []
    for t in sorted([p for p in ter if exceso(p["persona"]) > 0.05], key=lambda p: -exceso(p["persona"])):
        n = t["persona"]
        for _ in range(10):
            if exceso(n) <= 0.05:
                break
            proys = {}
            for i, m in enumerate(M):
                for c, d in asig[n][m].items():
                    if d > 0:
                        proys.setdefault(c, [0.0] * len(M))[i] = d
            mejor = None
            for c, dias in proys.items():
                baja = sum(max(0.0, uso[n][i] - cap[n][i]) - max(0.0, uso[n][i] - dias[i] - cap[n][i]) for i in range(len(M)))
                if baja < 0.05:
                    continue
                for o in ter:
                    on = o["persona"]
                    if o["perfil"] != t["perfil"] or on == n:
                        continue
                    if not all(not dias[i] or uso[on][i] + dias[i] <= cap[on][i] + 0.05 for i in range(len(M))):
                        continue
                    sc = baja * 1000 - sum(dias) + (0 if sum(uso[on]) else 5)
                    if mejor is None or sc > mejor["sc"]:
                        mejor = {"c": c, "dias": dias, "a": on, "sc": sc}
            if not mejor:
                movs.append({"de": n, "codigo": None, "exceso": exceso(n)})
                break
            for i, m in enumerate(M):
                if mejor["dias"][i]:
                    uso[n][i] -= mejor["dias"][i]; uso[mejor["a"]][i] += mejor["dias"][i]
                    asig[n][m].pop(mejor["c"], None); asig[mejor["a"]][m][mejor["c"]] = mejor["dias"][i]
            movs.append({"de": n, "codigo": mejor["c"], "a": mejor["a"], "dias": sum(mejor["dias"]), "exceso_despues": exceso(n)})
    return movs


def aplicar(R: dict, clave: str) -> list[dict] | None:
    import psycopg
    from motor import db
    with psycopg.connect(db.URL) as c:
        if c.execute("select 1 from gestor.meta where clave = %s", (clave,)).fetchone():
            return None
        movs = proponer(R)
        hechos = [m for m in movs if m.get("codigo")]
        cods = sorted({m["codigo"] for m in hechos})
        c.execute("create table if not exists gestor.respaldo (id serial primary key, motivo text, tabla text, data jsonb, creado timestamptz default now())")
        c.execute("""insert into gestor.respaldo (motivo, tabla, data) select %s, 'asignaciones', coalesce(jsonb_agg(to_jsonb(x)), '[]'::jsonb)
                     from gestor.asignaciones x where codigo = any(%s)""", (clave, cods))
        for m in hechos:
            de = c.execute("select dias from gestor.asignaciones where codigo = %s and persona = %s", (m["codigo"], m["de"])).fetchone()
            if not de:
                continue
            ya = c.execute("select dias from gestor.asignaciones where codigo = %s and persona = %s", (m["codigo"], m["a"])).fetchone()
            dias = dict(de[0] or {})
            if ya:
                for k, v in (ya[0] or {}).items():
                    dias[k] = float(dias.get(k, 0)) + float(v)
                c.execute("delete from gestor.asignaciones where codigo = %s and persona = %s", (m["codigo"], m["a"]))
            c.execute("delete from gestor.asignaciones where codigo = %s and persona = %s", (m["codigo"], m["de"]))
            c.execute("insert into gestor.asignaciones (codigo, persona, dias) values (%s, %s, %s)", (m["codigo"], m["a"], json.dumps(dias)))
        c.execute("insert into gestor.meta values (%s, '1') on conflict do nothing", (clave,))
        print(f"{clave}: " + json.dumps(movs, ensure_ascii=False), flush=True)
        return movs


def proponer_mensual(R: dict) -> list[dict]:
    """Primero proyectos completos (proponer); lo que quede, por mes: el exceso del proyecto más chico del mes
    pasa a un tercero del mismo rol con días libres ese mes (prefiere al que ya recibió ese proyecto)."""
    M = R["meses"]
    ter = [p for p in R["personas"] if p.get("tipo") == "tercero"]
    uso = {p["persona"]: [sum((p["asignado"].get(m) or {}).values()) for m in M] for p in ter}
    asig = {p["persona"]: {m: dict(p["asignado"].get(m) or {}) for m in M} for p in ter}
    cap = {p["persona"]: [float(x) for x in p["capacidad"]] for p in ter}
    out, receptor = [], {}
    for p in sorted(ter, key=lambda p: p["persona"]):
        n = p["persona"]
        for i, m in enumerate(M):
            ex = uso[n][i] - cap[n][i]
            if ex <= 0.05:
                continue
            for c, d in sorted(asig[n][m].items(), key=lambda kv: kv[1]):   # empieza por el proyecto más chico del mes
                if ex <= 0.05:
                    break
                mover = min(d, ex)
                cands = [o["persona"] for o in ter if o["perfil"] == p["perfil"] and o["persona"] != n and cap[o["persona"]][i] - uso[o["persona"]][i] > 0.05]
                cands.sort(key=lambda o: (receptor.get((n, c)) != o, c not in asig[o][m], -(cap[o][i] - uso[o][i])))
                for o in cands:
                    if ex <= 0.05 or mover <= 0.05:
                        break
                    x = min(mover, cap[o][i] - uso[o][i])
                    x = float(int(x * 2)) / 2 if x >= 0.5 else x
                    if x <= 0.05:
                        continue
                    uso[n][i] -= x; uso[o][i] += x; asig[n][m][c] -= x; asig[o][m][c] = asig[o][m].get(c, 0) + x
                    ex -= x; mover -= x; receptor[(n, c)] = o
                    out.append({"de": n, "rol": p["perfil"], "mes": m, "codigo": c, "dias": x, "a": o})
            if ex > 0.05:
                out.append({"de": n, "rol": p["perfil"], "mes": m, "codigo": None, "dias": ex, "a": None})
    return out
