// Utilidades compartidas: navegación, escenario y parámetros guardados en el navegador, cálculo.
const ESC_KEY = "gestor_ngr_escenario_v1", PAR_KEY = "gestor_ngr_parametros_v1";
const leer = k => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } };
const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const borrar = k => { try { localStorage.removeItem(k); } catch (e) {} };

async function calcular(extra = {}) {
  const body = {escenario: leer(ESC_KEY) || {}, parametros: leer(PAR_KEY) || {}, ...extra};
  const r = await fetch("/api/calcular", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) throw new Error("Error " + r.status);
  return r.json();
}

function nav(activa) {
  const tabs = [["/", "Consolidado"], ["/simulador", "Simulador"], ["/criterios", "Criterios"], ["/modelos", "Modelos"]];
  const mod = (leer(ESC_KEY) && Object.keys(leer(ESC_KEY)).length) || (leer(PAR_KEY) && Object.keys(leer(PAR_KEY)).length);
  const chip = mod ? '<button class="chip-cfg" id="chipReset" title="Volver a los datos y parámetros base">Escenario modificado · restaurar</button>'
                   : '<span class="chip-cfg base">Escenario base</span>';
  document.body.insertAdjacentHTML("afterbegin", `<nav class="tabs"><div class="brand">Gestor de Proyectos · TyT NGR</div>
    <div class="tablist">${tabs.map(([h, t]) => `<a href="${h}" class="${t === activa ? "on" : ""}">${t}</a>`).join("")}</div>${chip}</nav>`);
  const b = document.getElementById("chipReset");
  if (b) b.onclick = () => { if (confirm("¿Volver al escenario y parámetros base?")) { borrar(ESC_KEY); borrar(PAR_KEY); location.reload(); } };
}

const MES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"];
const fmtMes = m => MES[+m.slice(5) - 1] + "-" + m.slice(2, 4);
const fmtH = n => Math.round(n).toLocaleString("es-PE");
const fmtS = n => "S/ " + Math.round(n).toLocaleString("es-PE");
const fmtK = n => n >= 1e6 ? "S/ " + (n / 1e6).toFixed(2) + " M" : "S/ " + Math.round(n / 1000) + " k";
const fmtPct = n => n == null ? "–" : Math.round(n * 100) + "%";
const ETQ = {cubierto: "Cubierto", parcial: "Parcial", no_cubierto: "No cubierto", excluido: "Excluido"};
const COL = {cubierto: "#2e9e6a", parcial: "#e5a12a", no_cubierto: "#d64545", excluido: "#a7adb8"};
const COL_PERFIL = {"PM": "#1F3864", "Arquitecto": "#7b5ea7", "Desarrollador": "#2f7fc1", "Data Engineer": "#1a9a8f", "QA": "#d8833a", "Infra": "#6b7a8f"};
