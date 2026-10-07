"""Lógica de las pantallas que se puede probar sin Streamlit ni base: filtros, períodos y cabecera de la ficha."""
from datetime import timedelta

import formato

PESOS = ("ARS",)
DOLARES = ("USD", "USB")           # Dólares incluye USD y USD billete
OPCIONES_MONEDA = ("Pesos", "Dólares")
NOMBRE_MONEDA = {"ARS": "Pesos", "USD": "Dólares", "USB": "Dólares billete"}
SIMBOLO_MONEDA = {"ARS": "$", "USD": "USD", "USB": "USD"}
REGIONES = {"Arg": "Argentina", "Glo": "Global", "Latam": "Latinoamérica", "Eur": "Europa", "Bra": "Brasil",
            "Eeuu": "Estados Unidos"}
HORIZONTES = {"Cor": "Corto plazo", "Med": "Mediano plazo", "Lar": "Largo plazo", "Flex": "Flexible",
              "Sasig": "Sin asignar"}
TOLERANCIA_DIAS = 4                # fines de semana largos: no se avisa por menos de esto


def monedas_de(opcion):
    """'Pesos' -> ('ARS',) ; 'Dólares' -> ('USD', 'USB') ; 'Todos' -> None (sin filtro)."""
    return {"Pesos": PESOS, "Dólares": DOLARES}.get(opcion)


def filtrar_moneda(df, opcion, columna="moneda"):
    """Deja solo los fondos o clases de esa moneda ('Todos' no filtra)."""
    codigos = monedas_de(opcion)
    return df if codigos is None else df[df[columna].isin(codigos)]


def filtrar_por_rubros(df, rubros, columna="categoria_resumen"):
    """Filtro de la torta: sin rubros elegidos devuelve todo; con uno o más, solo los instrumentos de esos rubros."""
    rubros = list(rubros or [])
    return df if not rubros else df[df[columna].isin(rubros)]


def historia_desde(por_clase):
    """Primera fecha con cuotaparte numérica entre las clases activas.

    por_clase: DataFrame con 'primera' y 'ultima' (de las filas con cuotaparte numérica, nunca las que solo
    traen un estado). Activas = las que tienen datos hasta 10 días antes del último dato."""
    if por_clase.empty:
        return None
    activas = por_clase[por_clase["ultima"] >= por_clase["ultima"].max() - timedelta(days=10)]
    return activas["primera"].min()


def validar_periodo(desde, hasta):
    """Texto de aviso si el período no sirve (desde posterior o igual a hasta); None si está bien."""
    if desde is None or hasta is None:
        return "Elegí las dos fechas del período."
    if desde >= hasta:
        return (f"La fecha \"desde\" ({formato.fecha(desde)}) tiene que ser anterior a \"hasta\" "
                f"({formato.fecha(hasta)}). Corregí las fechas.")
    return None


def base_100(ser, desde, hasta=None):
    """Serie recortada a [desde, hasta] con cada clase en base 100 desde su primer valor de ese período."""
    s = ser[ser["fecha"] >= desde]
    if hasta is not None:
        s = s[s["fecha"] <= hasta]
    s = s.sort_values(["codigo_cafci", "fecha"]).copy()
    s["base 100"] = s.groupby("codigo_cafci")["cuotaparte"].transform(lambda x: x / x.iloc[0] * 100)
    return s


def clases_sin_cobertura(ser, desde, hasta):
    """Etiquetas de las clases que no tienen datos en todo el período (empiezan tarde o terminan antes)."""
    tol = timedelta(days=TOLERANCIA_DIAS)
    out = []
    for etiqueta, g in ser.groupby("etiqueta"):
        if g["fecha"].min() > desde + tol or g["fecha"].max() < hasta - tol:
            out.append(etiqueta)
    return sorted(out)


def _texto(x):
    return formato.texto_o_guion(x)


def cabecera_fondo(f):
    """Datos de la cabecera de la ficha como [(etiqueta, valor)]; ningún valor queda vacío (se muestra '—').

    'Clasificación CNV' sale solo de la ficha de la CNV; si no la informa, se muestra únicamente
    'Tipo de fondo (CAFCI)' (nunca el tipo de la CAFCI con la etiqueta CNV)."""
    moneda = f.get("moneda")
    simbolo = SIMBOLO_MONEDA.get(moneda, "")
    pn = formato.VACIO if formato._es_nulo(f.get("patrimonio_neto")) else f"{simbolo} {formato.entero(f['patrimonio_neto'])}".strip()
    fuentes = []
    if not formato._es_nulo(f.get("fecha_valores")):
        fuentes.append(f"CAFCI (valores al {formato.fecha(f['fecha_valores'])})")
    if not formato._es_nulo(f.get("fecha_cartera")):
        fuentes.append(f"CNV (cartera al {formato.fecha(f['fecha_cartera'])})")
    datos = [
        ("Cartera al", formato.fecha(f.get("fecha_cartera"))),
        ("Gerente", _texto(f.get("gerente"))),
        ("Depositaria", _texto(f.get("depositaria"))),
    ]
    if not formato._es_nulo(f.get("clasificacion_cnv")) and str(f["clasificacion_cnv"]).strip():
        datos.append(("Clasificación CNV", str(f["clasificacion_cnv"]).strip()))
    datos += [
        ("Tipo de fondo (CAFCI)", _texto(f.get("tipo_fondo"))),
        ("Moneda", NOMBRE_MONEDA.get(moneda, formato.VACIO)),
        ("Región", REGIONES.get(f.get("region"), _texto(f.get("region")))),
        ("Horizonte", HORIZONTES.get(f.get("horizonte"), _texto(f.get("horizonte")))),
        ("Patrimonio neto", pn),
        ("Fuente", " · ".join(fuentes) or formato.VACIO),
    ]
    return datos


def tematica(f):
    """(Descripción breve, Idea y temática) con la marca 'provisorio' si corresponde; '—' si falta."""
    marca = " (provisorio)" if f.get("tematica_provisoria") else ""
    def con_marca(x):
        t = _texto(x)
        return t if t == formato.VACIO else t + marca
    return con_marca(f.get("descripcion_breve")), con_marca(f.get("tematica"))
