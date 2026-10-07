"""Que la app no necesite "Reboot" después de cada publicación.

Streamlit trae los archivos nuevos pero puede seguir con un módulo propio viejo en memoria (error típico:
AttributeError en datos.ultimas_entradas). Cada pantalla llama a recarga.al_dia() antes de importar los módulos propios:
si el archivo de un módulo ya cargado cambió desde que se cargó, se vuelve a leer.
"""
import importlib
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
# En orden de dependencia (formato antes que calculos, que lo usa).
PROPIOS = ("formato", "estado", "horarios", "calculos", "informe", "acceso", "datos")
_vistos = {}


def _firma(ruta):
    e = ruta.stat()
    return (e.st_mtime_ns, e.st_size)


def al_dia(nombres=PROPIOS, carpeta=CARPETA):
    """Recarga los módulos propios cuyo archivo cambió. Devuelve los nombres recargados."""
    recargados = []
    for nombre in nombres:
        ruta = Path(carpeta) / f"{nombre}.py"
        modulo = sys.modules.get(nombre)
        if not ruta.exists() or (modulo is not None and not hasattr(modulo, "__file__")):
            continue                       # no existe o es un reemplazo de prueba
        firma = _firma(ruta)
        if modulo is None:
            _vistos[nombre] = firma        # todavía no se cargó: se va a leer el archivo de ahora
        elif _vistos.get(nombre) != firma:
            # Cargado antes de que cambie el archivo (o antes de que existiera este control): se vuelve a leer.
            importlib.reload(modulo)
            _vistos[nombre] = firma
            recargados.append(nombre)
    return recargados
