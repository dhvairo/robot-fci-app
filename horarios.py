"""Cuadro "Cuándo se actualiza cada dato" de la pantalla Inicio (lógica pura, sin base ni pantalla).

Los horarios salen de app/horarios.json, que genera herramientas/generar_horarios.py a partir de los
flujos de GitHub (.github/workflows). Todo en hora de Argentina.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARGENTINA = timezone(timedelta(hours=-3))
RUTA = Path(__file__).resolve().parent / "horarios.json"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

FLUJO_DIARIO = "robot_diario.yml"
FLUJO_CARTERAS = "robot_carteras.yml"
HORA_CORTE_NOCHE = 18          # el robot diario que corre de noche trae la versión del día; el de la tarde, la corregida


def cargar(ruta=RUTA):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))["corridas"]


def cargar_procesador(ruta=RUTA):
    """Las corridas del procesador de pedidos de fondos (pantalla "Sumar o quitar fondos")."""
    return json.loads(Path(ruta).read_text(encoding="utf-8")).get("procesador_pedidos", [])


def proxima_del_procesador(corridas, ahora):
    """Próximo momento (hora Argentina) en que corre el procesador de pedidos, o None si no hay corridas programadas."""
    return min((proxima(c, ahora) for c in corridas), default=None)


def texto_procesador(corridas):
    """'todos los días a las 08:00, 11:00, 14:00, 17:00 y 20:00' (o '—' si no hay corridas)."""
    if not corridas:
        return "—"
    horas = sorted(c["hora_ar"] for c in corridas)
    if all(c["dias_ar"] == corridas[0]["dias_ar"] for c in corridas):
        lista = horas[0] if len(horas) == 1 else ", ".join(horas[:-1]) + " y " + horas[-1]
        return f"{texto_dias(corridas[0]['dias_ar'])} a las {lista}"
    return "; ".join(texto_horario(c) for c in corridas)


def _hora(c):
    h, m = c["hora_ar"].split(":")
    return int(h), int(m)


def texto_dias(dias):
    """[0,1,2,3,4] -> 'lunes a viernes'; todos -> 'todos los días'; si no son seguidos, se listan."""
    dias = sorted(dias)
    if len(dias) == 7:
        return "todos los días"
    if len(dias) == 1:
        return DIAS[dias[0]]
    if dias == list(range(dias[0], dias[-1] + 1)):
        return f"{DIAS[dias[0]]} a {DIAS[dias[-1]]}"
    return ", ".join(DIAS[d] for d in dias[:-1]) + " y " + DIAS[dias[-1]]


def texto_horario(c):
    """'lunes a viernes 21:30'."""
    return f"{texto_dias(c['dias_ar'])} {c['hora_ar']}"


def proxima(c, ahora):
    """Próximo momento (hora Argentina) en que está programada la corrida `c`, después de `ahora`."""
    ahora = ahora.astimezone(ARGENTINA)
    h, m = _hora(c)
    for d in range(8):
        candidato = (ahora + timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0)
        if candidato > ahora and candidato.weekday() in c["dias_ar"]:
            return candidato
    raise ValueError("la corrida no tiene días programados")


def texto_momento(d):
    """'mié 07/10/2026 16:30'."""
    return f"{DIAS_CORTOS[d.weekday()]} {d:%d/%m/%Y %H:%M}"


def roles(corridas):
    """Separa las corridas por función: planilla de la noche (versión del día), planilla de la tarde
    (versión corregida) y carteras."""
    diarias = [c for c in corridas if c["flujo"] == FLUJO_DIARIO]
    return {"noche": next((c for c in diarias if _hora(c)[0] >= HORA_CORTE_NOCHE), None),
            "tarde": next((c for c in diarias if _hora(c)[0] < HORA_CORTE_NOCHE), None),
            "carteras": next((c for c in corridas if c["flujo"] == FLUJO_CARTERAS), None)}


def filas_cuadro(corridas, ahora, ultimas, formato_momento):
    """Filas del cuadro: [(dato, cuándo, última vez que entró un dato nuevo, próxima corrida)].

    `ultimas`: {'noche': momento, 'tarde': momento, 'carteras': momento, 'foto': momento} (puede faltar alguno).
    `formato_momento`: función que convierte un momento de la base en texto (hora de Argentina)."""
    r = roles(corridas)
    ult = lambda k: formato_momento(ultimas.get(k))      # noqa: E731
    sig = lambda c: texto_momento(proxima(c, ahora)) if c else "—"   # noqa: E731
    noche, tarde, carteras = r["noche"], r["tarde"], r["carteras"]
    proximas = [proxima(c, ahora) for c in (noche, tarde) if c]
    foto_cuando = " y ".join(c["hora_ar"] for c in (noche, tarde) if c) or "—"
    return [
        ("Planilla de la CAFCI: versión del día", texto_horario(noche) if noche else "—", ult("noche"), sig(noche)),
        ("Planilla de la CAFCI: versión corregida", texto_horario(tarde) if tarde else "—", ult("tarde"), sig(tarde)),
        ("Respaldo por la CNV si falla la CAFCI", "automático, en la misma corrida", "—", "—"),
        ("Carteras de la CNV", texto_horario(carteras) if carteras else "—", ult("carteras"), sig(carteras)),
        ("Foto de todas las clases", f"con cada planilla: {foto_cuando}",
         ult("foto"), texto_momento(min(proximas)) if proximas else "—"),
    ]
