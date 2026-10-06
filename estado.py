"""Semáforo de la base: ¿están llegando datos nuevos? (lógica pura, sin base ni pantalla)."""
from datetime import date, timedelta

# La CNV publica las carteras con demora: el 5/10/2026 la última disponible era la del 18/09 (17 días).
DIAS_CARTERA_OK = 21
DIAS_CARTERA_AVISO = 35


def dias_habiles_sin_datos(ultimo_valor, hoy):
    """Días hábiles (lunes a viernes) entre el último valor y hoy, sin contar hoy: los datos de hoy
    recién se publican a la noche."""
    n, d = 0, ultimo_valor + timedelta(days=1)
    while d < hoy:
        if d.weekday() < 5:
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
        if m == 0:
            out.append(("ok", f"Valores diarios al día (último dato: {f})."))
        elif m <= 2:
            out.append(("aviso", f"Último valor diario: {f}. Faltan {m} día(s) hábil(es): puede ser un feriado. "
                                 "Si sigue así, avisale a quien administra el robot."))
        else:
            out.append(("error", f"Hace {m} días hábiles que no llegan valores nuevos (último dato: {f}). "
                                 "El robot diario puede estar fallando."))
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
            out.append(("error", f"La última cartera es del {f} ({dias} días): el robot semanal puede estar fallando."))
    return out
