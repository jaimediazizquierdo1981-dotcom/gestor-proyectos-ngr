# Gestor de Proyectos NGR

Portafolio de proyectos de Tecnología y Transformación: tiempos, recursos por perfil, Capex y Opex, y viabilidad según la capacidad del equipo.

Stack: Python + FastAPI, frontend HTML/JS, desplegado en Render.

- `/` Consolidado: tabla de proyectos (incluir, prioridad, mes de inicio editables), pie de cobertura, real vs. proyectado (EVM) y mapa de uso por perfil.
- `/simulador`: oficina animada; los proyectos llegan al dispatcher y consumen las horas de cada persona mes a mes (`?auto=1` reproduce solo).
- `/criterios`: parámetros editables y guía para llenar cada campo.
- `/modelos`: base teórica (capacidad neta, Rayleigh, RCPSP, PERT + Monte Carlo, EVM).

Datos ficticios en `data/`. Los escenarios y parámetros se guardan en el navegador.

Local: `pip install -r requirements.txt && uvicorn app:app --reload`
