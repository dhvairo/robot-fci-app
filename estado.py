"""Semáforo de la base: ¿están llegando datos nuevos? (lógica pura, sin base ni pantalla)."""
from datetime import date, timedelta
import json
from pathlib import Path

_CALENDARIO = json.loads(Path(__file__).with_name("feriados_bcra.json").read_text(encoding="utf-8"))
COBERTURA_DESDE = date.fromisoformat(_CALENDARIO["desde"])
COBERTURA_HASTA = date.fromisoformat(_CALENDARIO["hasta"])
_FERIADOS = frozenset(date.fromisoformat(d) for d in _CALENDARIO["feriados"])

# La CNV publica las carteras con demora: el 5/10/2026 la última disponible era la del 18/09 (17 días).
DIAS_CARTERA_OK = 21
DIAS_CARTERA_AVISO = 35


def dias_habiles_sin_datos(ultimo_valor, hoy):
    """Cuenta lunes a viernes sin feriados BCRA ni extremos; None fuera de cobertura.

    Los datos de hoy recién se publican a la noche. Solo el intervalo interior
    necesita cobertura; un intervalo vacío tiene cero días pendientes.
    """
    n, d = 0, ultimo_valor + timedelta(days=1)
    if d < hoy and (d < COBERTURA_DESDE or hoy - timedelta(days=1) > COBERTURA_HASTA):
        return None
    while d < hoy:
        if d.weekday() < 5 and d not in _FERIADOS:
            n += 1
        d += timedelta(days=1)
    return n


def semaforo(hoy, ultimo_valor, ultima_cartera):
    """Devuelve [(nivel, mensaje)] con nivel 'ok', 'aviso' o 'error'."""
    out = []
    if ultimo_valor is None:
        out.append(("error", "No hay valores diarios cargados."))
    else:
        m = dias_habiles_sin_datos(ultimo_valor, hoy)
        f = f"{ultimo_valor:%d/%m/%Y}"
        if m is None:
            out.append(("aviso", f"Último valor diario: {f}. No se puede confirmar la cantidad de días hábiles "
                                 f"sin valores: calendario BCRA local con cobertura {COBERTURA_DESDE:%d/%m/%Y} "
                                 f"a {COBERTURA_HASTA:%d/%m/%Y}. Hace falta actualizar el calendario."))
        elif m == 0:
            out.append(("ok", f"Valores diarios al día (último dato: {f})."))
        elif m <= 2:
            out.append(("aviso", f"Último valor diario: {f}. Faltan {m} día(s) hábil(es) con valores "
                                 "(descontando feriados BCRA). Esta alerta mide la antigüedad del dato, "
                                 "no el estado de ejecución del robot."))
        else:
            out.append(("error", f"Hace {m} días hábiles que no llegan valores nuevos (último dato: {f}). "
                                 "Se descuentan feriados BCRA. Esta alerta mide la antigüedad del dato, "
                                 "no el estado de ejecución del robot."))
    if ultima_cartera is None:
        out.append(("error", "No hay carteras semanales cargadas."))
    else:
        dias = (hoy - ultima_cartera).days
        f = f"{ultima_cartera:%d/%m/%Y}"
        if dias <= DIAS_CARTERA_OK:
            out.append(("ok", f"Carteras semanales al día (última cartera: {f})."))
        elif dias <= DIAS_CARTERA_AVISO:
            out.append(("aviso", f"La última cartera es del {f} ({dias} días). Puede estar demorada la semanal."))
        else:
            out.append(("error", f"La última cartera es del {f} ({dias} días). Esta alerta mide la antigüedad "
                                 "del dato, no el estado de ejecución del robot."))
    return out
