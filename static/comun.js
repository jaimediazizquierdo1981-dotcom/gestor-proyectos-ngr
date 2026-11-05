// Utilidades compartidas: navegación, escenario y parámetros guardados en el navegador, cálculo.
const ESC_KEY = "gestor_ngr_escenario_v1", PAR_KEY = "gestor_ngr_parametros_v1";
const ordPrio = (a, b) => (a.prioridad ?? 1e9) - (b.prioridad ?? 1e9);   // sin prioridad: al final, en el orden del portafolio
const leer = k => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } };
const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const borrar = k => { try { localStorage.removeItem(k); } catch (e) {} };
// portafolio 2027 validado: la prioridad vuelve a ser la del archivo (se olvida la guardada en este navegador)
if (leer("gestor_ngr_portafolio") !== "valido_v1") {
  const e = leer(ESC_KEY) || {}; Object.values(e).forEach(v => delete v.prioridad); guardar(ESC_KEY, e); guardar("gestor_ngr_portafolio", "valido_v1");
}

// si la sesión vence, volver al login
const _fetch = window.fetch.bind(window);
window.fetch = async (...a) => { const r = await _fetch(...a); if (r.status === 401 && String(a[0]).startsWith("/api/") && !String(a[0]).startsWith("/api/auth")) location.href = "/login"; return r; };

async function calcular(extra = {}, ruta = "/api/calcular") {
  const body = {escenario: leer(ESC_KEY) || {}, parametros: leer(PAR_KEY) || {}, ...extra};
  const r = await fetch(ruta, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) throw new Error("Error " + r.status);
  return r.json();
}

const montecarlo = () => calcular({}, "/api/montecarlo");
function cargando(on, texto = "Calculando…") {
  let d = document.getElementById("cargando");
  if (!d) { d = document.createElement("div"); d.id = "cargando"; d.style.cssText = "position:fixed;right:20px;bottom:20px;background:#1F3864;color:#fff;padding:9px 16px;border-radius:10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.2);z-index:50"; document.body.appendChild(d); }
  d.textContent = texto; d.style.display = on ? "block" : "none";
}
function nav(activa) {
  const tabs = [["/", "Consolidado"], ["/colaboradores", "Colaboradores"], ["/hoja", "Hoja de trabajo"], ["/simulador", "Simulador"], ["/criterios", "Criterios"]];
  const mod = (leer(ESC_KEY) && Object.keys(leer(ESC_KEY)).length) || (leer(PAR_KEY) && Object.keys(leer(PAR_KEY)).length);
  const chip = mod ? '<button class="chip-cfg" id="chipReset" title="Volver a los datos y parámetros base">Escenario modificado · restaurar</button>'
                   : '<span class="chip-cfg base">Escenario base</span>';
  document.body.insertAdjacentHTML("afterbegin", `<nav class="tabs"><div class="brand">Gestor de Proyectos · TyT NGR</div>
    <div class="tablist">${tabs.map(([h, t]) => `<a href="${h}" class="${t === activa ? "on" : ""}">${t}</a>`).join("")}</div>${chip}<a href="/logout" class="salir">Salir</a></nav>`);
  const b = document.getElementById("chipReset");
  if (b) b.onclick = () => { if (confirm("¿Volver al escenario y parámetros base?")) { borrar(ESC_KEY); borrar(PAR_KEY); location.reload(); } };
}

const MES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"];
const fmtMes = m => MES[+m.slice(5) - 1] + "-" + m.slice(2, 4);
const activo = p => p.incluir && !p.on_hold;
const fmtD = n => (Math.abs(n) < 10 && Math.round(n) !== n ? n.toFixed(1).replace(".", ",") : Math.round(n).toLocaleString("es-PE"));
const fmtS = n => "S/ " + Math.round(n).toLocaleString("es-PE");
const fmtK = n => fmtS(n);   // siempre el número completo (sin k ni M)
const fmtPct = n => n == null ? "–" : Math.round(n * 100) + "%";
const ETQ = {cubierto: "Cubierto", parcial: "Parcial", no_cubierto: "No cubierto", on_hold: "On hold", excluido: "No activo"};
const COL = {cubierto: "#2e9e6a", parcial: "#e5a12a", no_cubierto: "#d64545", on_hold: "#8a8f99", excluido: "#a7adb8"};
const COL_PERFIL = {"Gestor de TI": "#1F3864", "Arquitecto": "#7b5ea7", "Analista Funcional": "#c2547a", "Analista Técnico": "#8a6d3b", "Desarrollador": "#2f7fc1", "Data Engineer": "#1a9a8f", "QA": "#d8833a", "Infra": "#6b7a8f", "Seguridad TI": "#b91c1c", "Lider Tecnico": "#0f766e", "Analytics engineer": "#0e7490", "Practicante Data": "#65a30d", "Devops": "#4338ca", "Soporte": "#a16207", "Demanda de Servicios": "#be185d"};
