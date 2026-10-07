"""Formato argentino para la app: fechas dd/mm/aaaa, coma decimal y punto de miles (auditoría B-04).

Funciones puras (no usan Streamlit ni la base), así se pueden probar sin pantalla.
"""
from datetime import date, datetime, timedelta, timezone

ARGENTINA = timezone(timedelta(hours=-3))

VACIO = "—"


def _es_nulo(x):
    return x is None or x != x  # None, NaN y NaT (NaN y NaT no son iguales a sí mismos)


def numero(x, decimales=2):
    """8.321 -> '8,32' ; 1234567.891 -> '1.234.567,89' ; vacío -> '—'."""
    if _es_nulo(x):
        return VACIO
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v != v:
        return VACIO
    if round(v, decimales) == 0:
        v = 0.0                        # evita '-0,00'
    s = f"{v:,.{decimales}f}"          # 1,234,567.89
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def entero(x):
    return numero(x, 0)


def numero_corto(x, minimo=2, maximo=4):
    """Como `numero`, pero sin ceros de más: 1.5 -> '1,50' ; 2.7731 -> '2,7731' ; 0.1815 -> '0,1815' ; vacío -> '—'.

    Se usa para honorarios y comisiones (se ven hasta 4 decimales) y, con minimo=0, para porcentajes cortos
    (78.51 con maximo=1 -> '78,5' ; 30.0 -> '30')."""
    if _es_nulo(x):
        return VACIO
    try:
        float(x)
    except (TypeError, ValueError):
        return str(x)
    for dec in range(minimo, maximo + 1):
        if abs(round(float(x), dec) - float(x)) < 1e-9 or dec == maximo:
            return numero(x, dec)


def fecha(d):
    """date, datetime, Timestamp o 'AAAA-MM-DD' -> '02/10/2026' ; vacío -> '—'."""
    if _es_nulo(d):
        return VACIO
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d[:10])
        except ValueError:
            return d
    return d.strftime("%d/%m/%Y")


def fecha_hora(d):
    """'02/10/2026 21:30' (en la hora que traiga el dato)."""
    if _es_nulo(d):
        return VACIO
    if isinstance(d, datetime) or hasattr(d, "hour"):
        return d.strftime("%d/%m/%Y %H:%M")
    return fecha(d)


def fecha_hora_ar(d):
    """Momento con zona horaria (por ejemplo, el de la base, en UTC) -> '02/10/2026 21:30' en hora de Argentina."""
    if _es_nulo(d):
        return VACIO
    if getattr(d, "tzinfo", None) is not None:
        d = d.astimezone(ARGENTINA)
    return fecha_hora(d)


def texto_o_guion(x):
    """Texto de un dato opcional (temática, descripción breve): vacío, nulo o solo espacios -> '—'."""
    if _es_nulo(x):
        return VACIO
    t = str(x).strip()
    return t or VACIO


def si_no(x):
    return VACIO if _es_nulo(x) else ("Sí" if x else "No")


def tabla(df, fechas=(), numeros=None, enteros=(), fechas_hora=(), booleanos=(), fechas_hora_ar=()):
    """Devuelve una copia del DataFrame con las columnas ya formateadas como texto en formato argentino.

    numeros: {columna: decimales}. Las columnas que no se nombran quedan como están."""
    df = df.copy()
    for c in fechas:
        df[c] = df[c].map(fecha)
    for c in fechas_hora:
        df[c] = df[c].map(fecha_hora)
    for c in fechas_hora_ar:
        df[c] = df[c].map(fecha_hora_ar)
    for c, dec in (numeros or {}).items():
        df[c] = df[c].map(lambda x, d=dec: numero(x, d))
    for c in enteros:
        df[c] = df[c].map(entero)
    for c in booleanos:
        df[c] = df[c].map(si_no)
    return df


def tabla_ordenable(df, fechas=(), numeros=None, enteros=(), cortos=None, si_no_=()):
    """Igual que `tabla`, pero devuelve un Styler: las columnas siguen siendo números (se pueden ordenar tocando el
    encabezado) y solo se VEN en formato argentino. Las que no se nombran quedan como están.

    cortos: {columna: (decimales mínimos, máximos)} (ver `numero_corto`)."""
    df = df.copy()
    formatos = {c: fecha for c in fechas}
    formatos.update({c: (lambda x, d=dec: numero(x, d)) for c, dec in (numeros or {}).items()})
    formatos.update({c: entero for c in enteros})
    formatos.update({c: (lambda x, m=m: numero_corto(x, *m)) for c, m in (cortos or {}).items()})
    formatos.update({c: si_no for c in si_no_})
    return df.style.format(formatos, na_rep=VACIO)


def plotly_es(fig, fechas_x=False):
    """Gráficos con coma decimal y punto de miles; si el eje x son fechas, las muestra dd/mm/aaaa."""
    fig.update_layout(separators=",.")
    if fechas_x:
        fig.update_xaxes(tickformat="%d/%m/%Y", hoverformat="%d/%m/%Y")
    return fig
