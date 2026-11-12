// Utilidades compartidas: navegación, escenario y parámetros guardados en el navegador, cálculo.
const ESC_KEY = "gestor_ngr_escenario_v1", PAR_KEY = "gestor_ngr_parametros_v1";
const ordPrio = (a, b) => (a.prioridad ?? 1e9) - (b.prioridad ?? 1e9);   // sin prioridad: al final, en el orden del portafolio
const leer = k => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } };
const guardar = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const borrar = k => { try { localStorage.removeItem(k); } catch (e) {} };
// portafolio 2027 validado: la prioridad vuelve a ser la del archivo (se olvida la guardada en este navegador)
if (leer("gestor_ngr_mov1902") !== "1") {   // la ficha PRY-27-19 se movió a PRY-27-02 (no a la 05)
  const e = leer(ESC_KEY) || {}; ["PRY-27-19", "PRY-27-05", "PRY-27-02"].forEach(k => delete e[k]); guardar(ESC_KEY, e); guardar("gestor_ngr_mov1902", "1");
}
if (leer("gestor_ngr_mov1905") !== "1") {   // la ficha PRY-27-19 se movió a PRY-27-05: se descartan ajustes locales viejos de ambas
  const e = leer(ESC_KEY) || {}; delete e["PRY-27-19"]; delete e["PRY-27-05"]; guardar(ESC_KEY, e); guardar("gestor_ngr_mov1905", "1");
}
if (leer("gestor_ngr_ini0827") !== "1") {   // Datos maestros vuelve a Ene-27
  const e = leer(ESC_KEY) || {}; if (e["PRY-27-08"]) { delete e["PRY-27-08"].inicio; delete e["PRY-27-08"].fin; } guardar(ESC_KEY, e); guardar("gestor_ngr_ini0827", "1");
}
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
    <div class="tablist">${tabs.map(([h, t]) => `<a href="${h}" class="${t === activa ? "on" : ""}">${t}</a>`).join("")}</div>${chip}<span class="quien" id="quien"></span><a href="/logout" class="salir">Salir</a></nav>`);
  fetch("/api/yo").then(r => r.ok ? r.json() : null).then(y => {   // usuario de la sesión y pestaña Usuarios (solo administrador)
    if (!y) return;
    if (y.es_admin) document.querySelector("nav.tabs .tablist").insertAdjacentHTML("beforeend",
      `<a href="/usuarios" class="${activa === "Usuarios" ? "on" : ""}">Usuarios</a><a href="/actividad" class="${activa === "Actividad" ? "on" : ""}">Actividad</a>`);
    if (y.solo_lectura) {
      document.body.classList.add("lectura");
      document.getElementById("quien").insertAdjacentHTML("beforebegin", '<span class="chip-lectura" title="Puedes consultar y simular; no puedes modificar información.">👁 Solo lectura</span>');
    }
    if (y.nombre) document.getElementById("quien").innerHTML = `${y.nombre}${y.emergencia ? "" : ' · <a href="/cambiar-clave">cambiar contraseña</a>'}`;
  }).catch(() => {});
  const b = document.getElementById("chipReset");
  if (b) b.onclick = () => { if (confirm("¿Volver al escenario y parámetros base?")) { borrar(ESC_KEY); borrar(PAR_KEY); location.reload(); } };
}

const MES = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"];
const fmtMes = m => MES[+m.slice(5) - 1] + "-" + m.slice(2, 4);
const activo = p => p.incluir && !p.on_hold && !!p.prioridad;   // sin prioridad no suma en los totales
const fmtD = n => (Math.abs(n) < 10 && Math.round(n) !== n ? n.toFixed(1).replace(".", ",") : Math.round(n).toLocaleString("es-PE"));
const fmtS = n => "S/ " + Math.round(n).toLocaleString("es-PE");
const fmtK = n => fmtS(n);   // siempre el número completo (sin k ni M)
const fmtPct = n => n == null ? "–" : Math.round(n * 100) + "%";
const ETQ = {cubierto: "Cubierto", parcial: "Parcial", no_cubierto: "No cubierto", on_hold: "On hold", sin_prioridad: "Sin prioridad", excluido: "No activo"};
const COL = {cubierto: "#2e9e6a", parcial: "#e5a12a", no_cubierto: "#d64545", on_hold: "#8a8f99", excluido: "#a7adb8"};
const COL_PERFIL = {"Gestor de TI": "#1F3864", "Arquitecto": "#7b5ea7", "Analista Funcional": "#c2547a", "Analista Técnico": "#8a6d3b", "Desarrollador": "#2f7fc1", "Data Engineer": "#1a9a8f", "Experto en Data": "#0b6e4f", "QA": "#d8833a", "Infra": "#6b7a8f", "Seguridad TI": "#b91c1c", "Lider Tecnico": "#0f766e", "Analytics engineer": "#0e7490", "Practicante Data": "#65a30d", "Devops": "#4338ca", "Soporte": "#a16207", "Demanda de Servicios": "#be185d"};

// ---------- cierre por inactividad: aviso al minuto 9, cierre al minuto 10 (compartido entre pestañas)
(() => {
  if (location.pathname === "/login" || location.pathname === "/cambiar-clave") return;
  const LIMITE = 10 * 60 * 1000, AVISO = 9 * 60 * 1000, K = "gestor_ngr_actividad";
  const marcar = () => { try { localStorage.setItem(K, String(Date.now())); } catch (e) {} };
  const ultima = () => { try { return +localStorage.getItem(K) || Date.now(); } catch (e) { return Date.now(); } };
  let caja = null, cerrando = false;
  const cambios = () => typeof window.hayCambiosSinGuardar === "function" && window.hayCambiosSinGuardar();
  function quitarAviso() { if (caja) { caja.remove(); caja = null; } }
  function mostrarAviso(seg) {
    if (!caja) {
      caja = document.createElement("div");
      caja.style.cssText = "position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:9999;display:grid;place-items:center";
      caja.innerHTML = `<div style="background:#fff;border-radius:14px;padding:22px 24px;max-width:430px;box-shadow:0 20px 50px rgba(0,0,0,.25);font:14px/1.5 system-ui,sans-serif;color:#1f2430">
        <b style="font-size:17px">¿Sigues ahí?</b>
        <p style="margin:8px 0 4px">Por seguridad, la aplicación se cerrará en <b id="idleSeg"></b> segundos por inactividad.</p>
        <p id="idleCambios" style="margin:4px 0;color:#b45309"></p>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button id="idleGuardar" style="display:none;font:inherit;border:1px solid #1f3864;background:#fff;color:#1f3864;border-radius:9px;padding:8px 12px;cursor:pointer">💾 Guardar cambios</button>
          <button id="idleSeguir" style="font:inherit;border:0;background:#1f3864;color:#fff;border-radius:9px;padding:8px 14px;cursor:pointer">Seguir trabajando</button>
        </div></div>`;
      document.body.appendChild(caja);
      caja.querySelector("#idleSeguir").onclick = () => { marcar(); quitarAviso(); fetch("/api/yo").catch(() => {}); };
      caja.querySelector("#idleGuardar").onclick = async () => { marcar(); quitarAviso(); await window.guardarCambiosPendientes(); };
    }
    const hay = cambios();
    caja.querySelector("#idleSeg").textContent = seg;
    caja.querySelector("#idleCambios").textContent = hay ? "Tienes cambios sin guardar en la Hoja de trabajo: te sugerimos guardarlos ahora." : "";
    caja.querySelector("#idleGuardar").style.display = hay ? "" : "none";
  }
  async function cerrar() {
    if (cerrando) return; cerrando = true;
    if (cambios() && typeof window.guardarCambiosPendientes === "function") { try { await window.guardarCambiosPendientes(); } catch (e) {} }
    location.href = "/logout?motivo=inactividad";
  }
  ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "wheel"].forEach(ev => addEventListener(ev, () => { if (!caja) marcar(); }, {passive: true}));
  marcar();
  setInterval(() => {
    const quieto = Date.now() - ultima();
    if (quieto >= LIMITE) cerrar();
    else if (quieto >= AVISO) mostrarAviso(Math.ceil((LIMITE - quieto) / 1000));
    else quitarAviso();
  }, 1000);
  // mientras haya actividad, renueva la sesión del servidor (máximo una vez por minuto)
  let ping = 0;
  setInterval(() => { if (Date.now() - ultima() < 60000 && Date.now() - ping > 60000) { ping = Date.now(); fetch("/api/yo").catch(() => {}); } }, 15000);
})();
