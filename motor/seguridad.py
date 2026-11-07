"""Login con usuario, contraseña y captcha propio. Sesión firmada en cookie (HMAC)."""
from __future__ import annotations

import hashlib
import hmac
import os
import random
import secrets
import time

APP_USER = os.environ.get("APP_USER", "").strip()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRETO = os.environ.get("SESSION_SECRET", "") or secrets.token_hex(32)
DURACION = 8 * 3600
SEGURA = os.environ.get("DEV") != "1"   # cookies solo por HTTPS (en local: DEV=1)
INTENTOS: dict[str, list[float]] = {}   # ip -> marcas de tiempo de intentos fallidos
LIBRES = {"/login", "/api/auth/login", "/api/auth/captcha", "/favicon.ico", "/static/favicon.svg", "/static/favicon.png"}
HAY_USUARIOS = False   # lo prende app.py si la tabla gestor.usuarios tiene registros
ITER = 260_000


def activo() -> bool:
    return bool(APP_USER and APP_PASSWORD) or HAY_USUARIOS


# ---------- contraseñas: nunca se guardan; solo PBKDF2-SHA256 con sal aleatoria por usuario
def hash_clave(clave: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), ITER).hex()
    return f"pbkdf2_sha256${ITER}${sal}${h}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        alg, it, sal, h = guardado.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), int(it)).hex()
        return alg == "pbkdf2_sha256" and hmac.compare_digest(calc, h)
    except (ValueError, AttributeError):
        return False


def clave_temporal() -> str:
    a = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(a) for _ in range(10)) + secrets.choice("!#$%*?")


def clave_valida(clave: str) -> str | None:
    if len(clave) < 8:
        return "La contraseña debe tener al menos 8 caracteres."
    if not any(c.isdigit() for c in clave) or not any(c.isalpha() for c in clave):
        return "La contraseña debe combinar letras y números."
    return None


def firmar(t: str) -> str:
    return hmac.new(SECRETO.encode(), t.encode(), hashlib.sha256).hexdigest()


def usuario_sesion(cookie: str | None) -> str | None:
    """Usuario de una cookie de sesión válida (o None)."""
    try:
        u, vence, f = (cookie or "").split("|")
        return u if hmac.compare_digest(f, firmar(f"{u}|{vence}")) and int(vence) > time.time() else None
    except ValueError:
        return None


def sesion_valida(cookie: str | None) -> bool:
    return True if not activo() else usuario_sesion(cookie) is not None


def nueva_sesion(usuario: str = "") -> tuple[str, int]:
    u = (usuario or APP_USER).lower()
    vence = int(time.time()) + DURACION
    return f"{u}|{vence}|{firmar(f'{u}|{vence}')}", DURACION


def bloqueado(ip: str) -> bool:
    ahora = time.time()
    INTENTOS[ip] = [t for t in INTENTOS.get(ip, []) if ahora - t < 300]
    return len(INTENTOS[ip]) >= 5


def fallo(ip: str) -> None:
    INTENTOS.setdefault(ip, []).append(time.time())


def credenciales_ok(usuario: str, clave: str) -> bool:
    """Usuario de emergencia definido en variables de entorno (APP_USER / APP_PASSWORD)."""
    return bool(APP_USER and APP_PASSWORD) and hmac.compare_digest(usuario.lower(), APP_USER.lower()) and hmac.compare_digest(clave, APP_PASSWORD)


def captcha() -> tuple[str, str, str]:
    """Devuelve (svg, cookie, codigo)."""
    codigo = "".join(secrets.choice("ABCDEFGHJKLMNPRSTUVWXYZ23456789") for _ in range(5))
    vence = str(int(time.time()) + 300)
    r = random.Random()
    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="180" height="56" viewBox="0 0 180 56">',
         '<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#eef2ff"/><stop offset="1" stop-color="#e0f2fe"/></linearGradient></defs>',
         '<rect width="180" height="56" rx="10" fill="url(#g)"/>']
    for _ in range(6):
        p.append(f'<path d="M{r.randint(0,40)},{r.randint(5,50)} C{r.randint(40,90)},{r.randint(0,56)} {r.randint(90,140)},{r.randint(0,56)} {r.randint(140,180)},{r.randint(5,50)}" '
                 f'stroke="hsl({r.randint(200,280)},50%,70%)" stroke-width="{r.uniform(1,2):.1f}" fill="none"/>')
    for _ in range(35):
        p.append(f'<circle cx="{r.randint(0,180)}" cy="{r.randint(0,56)}" r="{r.uniform(.6,1.5):.1f}" fill="#94a3b8"/>')
    for i, ch in enumerate(codigo):
        x, y, rot = 22 + i * 30 + r.randint(-3, 3), 38 + r.randint(-5, 5), r.randint(-25, 25)
        p.append(f'<text x="{x}" y="{y}" font-family="Verdana,Arial" font-size="{r.randint(24,30)}" font-weight="700" '
                 f'fill="hsl({r.randint(215,265)},60%,{r.randint(28,42)}%)" transform="rotate({rot} {x} {y})">{ch}</text>')
    p.append("</svg>")
    return "".join(p), f"{vence}|{firmar(codigo + '|' + vence)}", codigo


def captcha_ok(cookie: str, escrito: str) -> bool:
    try:
        vence, f = (cookie or "").split("|")
        return int(vence) > time.time() and hmac.compare_digest(f, firmar(escrito.strip().upper() + "|" + vence))
    except ValueError:
        return False
