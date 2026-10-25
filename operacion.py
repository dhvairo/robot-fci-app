"""Metadatos de Actions, solo lectura; caché compartida entre sesiones por 600 s."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Lock
from time import monotonic

import requests
import streamlit as st

REPO = "dhvairo/robot-fci"
TTL = 600
ARGENTINA = timezone(timedelta(hours=-3))
FLUJOS = {"diario": "robot_diario.yml", "carteras": "robot_carteras.yml",
          "hechos CNV": "robot_hechos_cnv.yml"}


def interpretar(run):
    """El éxito de Actions no acredita datos nuevos; nunca depende de la base."""
    estado, resultado = run.get("status"), run.get("conclusion")
    if estado in {"queued", "waiting", "pending", "requested"}:
        return "aviso", "Pendiente"
    if estado == "in_progress":
        return "aviso", "En curso"
    if estado == "completed":
        if resultado == "success":
            return "ok", "Éxito de ejecución; no confirma datos nuevos"
        if resultado == "cancelled":
            return "aviso", "Cancelada"
        if resultado in {"failure", "timed_out", "action_required", "startup_failure", "stale"}:
            return "error", "Fallo de ejecución (" + resultado + ")"
    return "aviso", "Estado desconocido"


def consultar(flujo, token, get=requests.get):
    enlace = f"https://github.com/{REPO}/actions/workflows/{flujo}"
    fila = {"nivel": "aviso", "estado": "Estado desconocido", "fecha": "—", "enlace": enlace}
    if not token:
        return {**fila, "estado": "Estado desconocido: falta GH_TOKEN o GITHUB_TOKEN en la app"}
    try:
        respuesta = get(f"https://api.github.com/repos/{REPO}/actions/workflows/{flujo}/runs",
                        params={"branch": "main", "per_page": 1},
                        headers={"Authorization": f"Bearer {token}",
                                 "Accept": "application/vnd.github+json",
                                 "X-GitHub-Api-Version": "2022-11-28"},
                        timeout=8, allow_redirects=False)
        if respuesta.status_code in {401, 403, 404}:
            return {**fila, "estado": "Estado desconocido: acceso no disponible; Dani debe revisar "
                    "acceso al repositorio privado y permiso Actions: lectura del token existente"}
        respuesta.raise_for_status()
        if respuesta.status_code != 200:
            raise ValueError("Respuesta inesperada")
        runs = respuesta.json()["workflow_runs"]
        if not isinstance(runs, list):
            raise ValueError("Formato inesperado")
        if not runs:
            return {**fila, "estado": "Estado desconocido: sin ejecuciones en main"}
        run = runs[0]
        fecha = datetime.fromisoformat(run.get("run_started_at") or run["created_at"])
        if fecha.tzinfo is None or not isinstance(run["id"], int) or run["id"] <= 0:
            raise ValueError("Metadatos incompletos")
        nivel, estado = interpretar(run)
        return {"nivel": nivel, "estado": estado,
                "fecha": fecha.astimezone(ARGENTINA).strftime("%d/%m/%Y %H:%M:%S"),
                "enlace": f"https://github.com/{REPO}/actions/runs/{run['id']}"}
    except requests.Timeout:
        return {**fila, "estado": "Estado desconocido: tiempo de consulta agotado"}
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, IndexError):
        # No exponer excepciones, cabeceras ni cuerpos: pueden contener credenciales.
        return {**fila, "estado": "Estado desconocido: no se pudo consultar Actions"}


class CacheOperacion:
    def __init__(self):
        self.lock = Lock()
        self.clave = None
        self.vence = 0
        self.resultado = None

    def obtener(self, token, consultar_flujo=consultar, reloj=monotonic, ahora=None):
        clave = sha256((token or "").encode("utf-8")).digest()
        with self.lock:
            if self.clave != clave or reloj() >= self.vence:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    pendientes = {nombre: pool.submit(consultar_flujo, flujo, token)
                                  for nombre, flujo in FLUJOS.items()}
                    filas = {nombre: tarea.result() for nombre, tarea in pendientes.items()}
                fecha = (ahora or datetime.now(timezone.utc)).astimezone(ARGENTINA)
                self.resultado = {"flujos": filas, "consultado": fecha,
                                  "vence": fecha + timedelta(seconds=TTL)}
                self.clave, self.vence = clave, reloj() + TTL
            return self.resultado


@st.cache_resource
def cache_compartida():
    return CacheOperacion()


def obtener(secrets):
    from consulta_puntual import token_configurado
    return cache_compartida().obtener(token_configurado(secrets))
