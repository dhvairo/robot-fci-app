"""Lógica de las pantallas que se puede probar sin Streamlit ni base: filtros, períodos y cabecera de la ficha."""
import unicodedata
from datetime import timedelta

import pandas as pd

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


# ───────── Resumen masivo de fondos (pedido 3.1 de octubre) ─────────

# Las 13 categorías resumen, en el orden de la solapa "Resumen" del Excel de carteras.
CATEGORIAS_RESUMEN = ("Disponibilidades", "ON", "Bono corporativo", "Deuda soberana", "Deuda sub-soberana", "Pagaré",
                      "Cheques", "Plazo fijo", "Caución", "Fideicomisos financieros", "FCI", "Acciones",
                      "Otros activos")
ETIQUETA_CATEGORIA = {"Deuda sub-soberana": "Deuda sub-soberana / provincial"}
AJUSTE = "Ajuste a 100% (pasivos/redondeo)"
MONEDA_RESUMEN = {"ARS": "ARS", "USD": "USD", "USB": "USD billete"}
# Las notas al pie de la solapa "Resumen" del Excel (la última se adaptó: el gráfico ahora es el del informe).
NOTAS_RESUMEN = (
    "Patrimonio neto en la moneda de cada fondo (ARS o USD): no es comparable entre filas sin convertir a una misma moneda.",
    "Columnas de tipo de activo: suma de '% del PN' por 'Categoría (resumen)' de cada cartera. Las tenencias 1°-3° salen "
    "del detalle por moneda-indexación (rubro + moneda + indexación).",
    "Sub-soberano = provincias. Indexación de soberanos en pesos: CER = Boncer, Lecer, Discount (DICP) y serie TZX "
    "(TZXO7 asignado CER por nomenclatura). Dual = TXMD9. Dólar linked* = D31M7 (por nomenclatura, a verificar). Los "
    "títulos corporativos se clasifican solo por moneda: la CNV no informa si son tasa fija, variable o ajustables.",
    "'Ajuste a 100%': pasivos no detallados por la CNV (negativo = fondo apalancado) más redondeos.",
    "Gráfico del informe: cada fondo es una porción de igual tamaño (1/N, con N = cantidad de fondos de esa moneda).",
)
COLUMNAS_TENENCIA = (("1° tenencia (rubro / moneda / indexación)", "% 1°"), ("2° tenencia", "% 2°"),
                     ("3° tenencia", "% 3°"))


def _pct(x):
    return None if formato._es_nulo(x) else float(x)


def resumen_masivo(fondos, carteras, sumas, tenencias):
    """La tabla de la solapa "Resumen" del Excel, para los fondos elegidos (una fila por fondo, en el orden de `fondos`).

    fondos:    codigo_cnv, nombre, moneda, clasificacion_cnv, descripcion_breve, tematica, tematica_provisoria
    carteras:  codigo_cnv, fecha_cartera, patrimonio          (la última cartera de cada fondo)
    sumas:     codigo_cnv, categoria_resumen, pct              (suma del % del PN por categoría resumen)
    tenencias: codigo_cnv, posicion (1 a 3), categoria_detallada, pct_pn

    Los porcentajes quedan en puntos (94,44 = 94,44%). Las columnas que empiezan con "_" son para el informe."""
    import pandas as pd
    cart = {int(r["codigo_cnv"]): r for _, r in carteras.iterrows()}
    por_cat = {}
    for _, r in sumas.iterrows():
        por_cat.setdefault(int(r["codigo_cnv"]), {})[r["categoria_resumen"]] = float(r["pct"])
    ten = {}
    for _, r in tenencias.iterrows():
        ten.setdefault(int(r["codigo_cnv"]), {})[int(r["posicion"])] = (r["categoria_detallada"], _pct(r["pct_pn"]))
    filas = []
    for _, f in fondos.iterrows():
        cod = int(f["codigo_cnv"])
        c, cats = cart.get(cod), por_cat.get(cod, {})
        fila = {"Fondo": f["nombre"], "Moneda": MONEDA_RESUMEN.get(f["moneda"], formato.VACIO),
                "Clasificación CNV": _texto(f.get("clasificacion_cnv")),   # solo la de la ficha de la CNV, nunca la de la CAFCI
                "Descripción breve": _texto(f.get("descripcion_breve")),
                "Patrimonio neto": None if c is None else _pct(c["patrimonio"])}
        for cat in CATEGORIAS_RESUMEN:
            fila[ETIQUETA_CATEGORIA.get(cat, cat)] = cats.get(cat, 0.0)
        fila[AJUSTE] = round(100 - sum(cats.get(cat, 0.0) for cat in CATEGORIAS_RESUMEN), 4)
        for pos, (col, col_pct) in enumerate(COLUMNAS_TENENCIA, start=1):
            nombre, pct = ten.get(cod, {}).get(pos, (formato.VACIO, None))
            fila[col], fila[col_pct] = nombre, pct
        fila["Cartera al"] = None if c is None else c["fecha_cartera"]
        fila["_codigo"], fila["_moneda"] = cod, f["moneda"]
        fila["_idea"] = tematica(f.to_dict())[1]
        fila["_idea_pdf"] = _texto(f.get("tematica"))        # el PDF para clientes no lleva la marca "(provisorio)"
        filas.append(fila)
    return pd.DataFrame(filas)


def columnas_resumen(tabla):
    """Columnas de `resumen_masivo` que se ven en pantalla ('Cartera al' solo si las fechas de cartera difieren)."""
    cols = [c for c in tabla.columns if not c.startswith("_")]
    if tabla["Cartera al"].nunique(dropna=False) <= 1:
        cols.remove("Cartera al")
    return cols


# ───────── Gestión y eficiencia (pedido 3.2 de octubre) ─────────

PESO_RENDIMIENTO = 0.70
PESO_HONORARIO = 0.30
MINIMO_GRUPO = 3
MINIMO_ATIPICOS = 8        # con menos clases en el grupo no se marca ninguna como atípica
FACTOR_VALLA = 3           # entre esta valla y la extrema: entra al ranking con una marca "revisar"
FACTOR_VALLA_EXTREMA = 10  # más allá de esta valla: afuera, error de datos probable
DESCARGO_EFICIENCIA = ("Ranking informativo. No constituye recomendación de inversión. Los rendimientos ya son netos de "
                       "honorarios y gastos; los rendimientos pasados no garantizan rendimientos futuros.")
MOTIVO_HONORARIO_CERO = "Honorario 0% informado, verificar"
MOTIVO_ATIPICO = "Error de datos probable, verificar"
MARCA_MUY_ALTO = "Rendimiento muy alto, revisar"
MARCA_MUY_BAJO = "Rendimiento muy bajo, revisar"
MOTIVO_ESTADO = {"(sin patrimonio)": "Sin patrimonio", "(sin datos recientes)": "Sin datos recientes",
                 "(fuera de la planilla)": "Fuera de la planilla"}


def clases_a_mostrar(clases, todas=False):
    """Por defecto, Clase A y B de cada fondo (si el fondo no las tiene, las que haya); con `todas`, todas las clases."""
    if todas or clases.empty:
        return clases
    ab = clases["clase"].str.contains(r"Clase [AB]\b", regex=True)
    tiene = ab.groupby(clases["codigo_cnv"]).transform("any")
    return clases[ab | ~tiene]


def orden_inicial(clases):
    """De mayor a menor honorario de la sociedad gerente (los sin dato al final); a igualdad, por nombre."""
    return clases.sort_values(["honorarios_sg", "clase"], ascending=[False, True], na_position="last",
                              kind="stable").reset_index(drop=True)


def grupo_moneda(moneda):
    return "Pesos" if moneda in PESOS else "Dólares"


def motivo_exclusion(c):
    """Por qué una clase no entra al ranking (None si entra). Cualquier estado de la foto la deja afuera."""
    estado = c.get("marca_estado")
    if not formato._es_nulo(estado) and str(estado).strip():
        estado = str(estado).strip()
        return MOTIVO_ESTADO.get(estado, f"Con marca {estado} en la planilla (suspendido o en liquidación)")
    if formato._es_nulo(c.get("rend_12m")):
        return "Sin 12 meses de historia"
    if formato._es_nulo(c.get("honorarios_sg")):
        return "Sin honorario de la sociedad gerente informado"
    if formato._es_nulo(c.get("tipo_fondo")):
        return "Sin tipo de fondo"
    return None


def _percentil(serie, mas_alto_es_mejor):
    """0 a 100 dentro del grupo: el peor vale 0 y el mejor 100; los empatados comparten el promedio de sus lugares."""
    n = len(serie)
    p = (serie.rank(method="average") - 1) / (n - 1) * 100       # rank 1 = el valor más bajo
    return p if mas_alto_es_mejor else 100 - p


def ejecutar_eficiencia(clases):
    """Puntaje de eficiencia 0-100 = 70% percentil del rendimiento 12m (más alto, mejor) + 30% percentil del honorario
    de la sociedad gerente (más bajo, mejor), SOLO dentro del mismo tipo de fondo y moneda. Los demás costos no entran.

    clases: DataFrame con tipo_fondo, moneda, rend_12m, honorarios_sg, marca_estado (y lo que se quiera mostrar).
    Devuelve (ranking, afuera):
      ranking: las clases comparables con 'puntaje', 'posicion' (1 = la mejor de su grupo; empatadas comparten lugar),
               'en_grupo' (cuántas clases tiene el grupo), 'grupo' (texto) y 'marca' (rendimiento muy alto o muy bajo, a
               revisar; vacía si no), ordenado por puntaje de mayor a menor.
      afuera:  las demás, con 'motivo' (estado, sin 12 meses, sin honorario, honorario 0%, error de datos probable, o grupo
               con menos de 3 clases). Los percentiles se calculan sin las que quedan afuera."""
    clases = clases.copy().reset_index(drop=True)
    clases["motivo"] = [motivo_exclusion(r) for _, r in clases.iterrows()]
    clases["grupo"] = [f"{t} · {grupo_moneda(m)}" for t, m in zip(clases["tipo_fondo"], clases["moneda"])]
    # Honorario de la sociedad gerente 0%: puede ser un dato faltante de la CAFCI, queda afuera (sigue visible en la tabla).
    cero = clases["motivo"].isna() & (clases["honorarios_sg"] == 0)
    clases.loc[cero, "motivo"] = MOTIVO_HONORARIO_CERO
    # Rendimiento atípico en dos niveles, dentro de cada grupo (IQR = Q3 - Q1):
    #   más allá de Q3 + 10 x IQR o de Q1 - 10 x IQR: afuera (error de datos probable);
    #   entre la valla de 3 x IQR y la de 10 x IQR: entra al ranking con una marca "revisar".
    # Q1 y Q3 se calculan con las clases que siguen en carrera (sin las de honorario 0% ni las de estado). Un grupo de
    # menos de MINIMO_ATIPICOS de esas clases no marca ni saca ninguna.
    en_carrera = clases[clases["motivo"].isna()]
    rend = en_carrera.groupby("grupo")["rend_12m"]
    q1, q3 = rend.transform(lambda s: s.quantile(0.25)), rend.transform(lambda s: s.quantile(0.75))
    iqr = q3 - q1
    r12 = en_carrera["rend_12m"]
    grande = rend.transform("size") >= MINIMO_ATIPICOS
    extrema = ((r12 > q3 + FACTOR_VALLA_EXTREMA * iqr) | (r12 < q1 - FACTOR_VALLA_EXTREMA * iqr)) & grande
    clases.loc[extrema[extrema].index, "motivo"] = MOTIVO_ATIPICO
    clases["marca"] = ""
    alto = (r12 > q3 + FACTOR_VALLA * iqr) & ~extrema & grande
    bajo = (r12 < q1 - FACTOR_VALLA * iqr) & ~extrema & grande
    clases.loc[alto[alto].index, "marca"] = MARCA_MUY_ALTO
    clases.loc[bajo[bajo].index, "marca"] = MARCA_MUY_BAJO
    ok = clases[clases["motivo"].isna()].copy()
    chicos = ok.groupby("grupo")["grupo"].transform("size") < MINIMO_GRUPO
    clases.loc[ok[chicos].index, "motivo"] = f"Grupo con menos de {MINIMO_GRUPO} clases comparables"
    ok = ok[~chicos].copy()
    if ok.empty:
        ok["puntaje"], ok["posicion"], ok["en_grupo"] = [], [], []
    else:
        ok["en_grupo"] = ok.groupby("grupo")["grupo"].transform("size")
        ok["puntaje"] = (PESO_RENDIMIENTO * ok.groupby("grupo")["rend_12m"].transform(lambda s: _percentil(s, True))
                         + PESO_HONORARIO * ok.groupby("grupo")["honorarios_sg"].transform(lambda s: _percentil(s, False)))
        ok["posicion"] = ok.groupby("grupo")["puntaje"].rank(method="min", ascending=False).astype(int)
        ok = ok.sort_values(["puntaje", "honorarios_sg", "clase"], ascending=[False, True, True], kind="stable")
    afuera = clases[clases["motivo"].notna()]
    return ok.drop(columns="motivo").reset_index(drop=True), afuera.reset_index(drop=True)


# ───────── Sumar o quitar fondos (pedidos de la app al robot) ─────────

ACCIONES_PEDIDO = {"seguir": "Seguir", "dejar": "Dejar de seguir", "editar_tematica": "Editar descripción y temática"}
ESTADOS_PEDIDO = {"pendiente": "Pendiente", "listo": "Listo", "problema": "Problema"}
MAX_DESCRIPCION = 500          # mismos topes que la política de la base (base_de_datos/usuario_pedidos.sql)
MAX_IDEA = 2000
MAX_DIGITOS_ID_FICHA = 9


def _sin_tildes(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or "").lower()) if unicodedata.category(c) != "Mn")


def filtrar_catalogo(df, texto="", moneda="Todas", tipos=(), rubros=(), situacion="Todos"):
    """Buscador del catálogo completo. `texto`: todas las palabras tienen que estar en el nombre del fondo o de su gerente
    (sin importar mayúsculas ni tildes). `moneda`: 'Todas', 'Pesos' o 'Dólares'. `situacion`: 'Todos', 'Seguidos' o
    'No seguidos'. Sin tipos o rubros elegidos no se filtra por ellos."""
    out = filtrar_moneda(df, moneda)
    if tipos:
        out = out[out["tipo_fondo"].isin(tipos)]
    if rubros:
        out = out[out["rubro"].isin(rubros)]
    if situacion == "Seguidos":
        out = out[out["seguido"]]
    elif situacion == "No seguidos":
        out = out[~out["seguido"]]
    palabras = _sin_tildes(texto).split()
    if palabras:
        pajar = (out["nombre"].map(_sin_tildes) + " " + out["gerente"].map(_sin_tildes))
        for p in palabras:
            out = out[pajar.loc[out.index].str.contains(p, regex=False)]
    return out.sort_values("nombre", kind="stable")


def validar_pedido(accion, descripcion=None, idea=None, id_ficha=None):
    """Limpia y controla lo que se escribió en la pantalla. Devuelve (datos, error): `datos` = {'accion', 'descripcion',
    'idea', 'id_ficha'} (los vacíos quedan en None) y `error` = texto para mostrar, o None si está todo bien."""
    if accion not in ACCIONES_PEDIDO:
        return None, "Acción desconocida."
    texto = lambda x: x.strip() if isinstance(x, str) else ""      # noqa: E731  (un dato vacío de la base llega como None o NaN)
    desc, idea = texto(descripcion) or None, texto(idea) or None
    ficha = (str(id_ficha).strip().replace(".", "") if id_ficha is not None and id_ficha == id_ficha else "") or None
    if accion == "dejar":
        return {"accion": accion, "descripcion": None, "idea": None, "id_ficha": None}, None
    if desc and len(desc) > MAX_DESCRIPCION:
        return None, f"La descripción breve tiene {len(desc)} caracteres: el máximo es {MAX_DESCRIPCION}."
    if idea and len(idea) > MAX_IDEA:
        return None, f"La idea y temática tiene {len(idea)} caracteres: el máximo es {MAX_IDEA}."
    if ficha is not None:
        if not ficha.isdigit() or int(ficha) == 0 or len(ficha) > MAX_DIGITOS_ID_FICHA:
            return None, "El ID de la ficha CNV son solo números (por ejemplo 63491); está en la dirección de la ficha del fondo en la web de la CNV."
        ficha = int(ficha)
    if accion == "editar_tematica":
        ficha = None
    return {"accion": accion, "descripcion": desc, "idea": idea, "id_ficha": ficha}, None


def motivo_pide_id(motivo):
    """¿El problema se arregla cargando el ID de la ficha CNV? (no se encontró la ficha, no sirve o es de otra gerente)"""
    return "ficha cnv" in str(motivo or "").lower()


def preparar_pedidos(df):
    """Lista de pedidos para mostrar: agrega 'accion_texto', 'estado_texto' (con "reintentado" si después se pidió de
    nuevo lo mismo) y 'reintentable' (un 'seguir' con problema por falta de ID que nadie volvió a pedir).
    Ordenados del más nuevo al más viejo."""
    df = df.sort_values("id", ascending=False).copy()
    df["accion_texto"] = df["accion"].map(ACCIONES_PEDIDO).fillna(df["accion"])
    repetido = pd.Series([bool(((df["codigo_cnv"] == p.codigo_cnv) & (df["accion"] == p.accion) & (df["id"] > p.id)).any())
                          for p in df.itertuples()], index=df.index, dtype=bool)
    problema = df["estado"] == "problema"
    df["reintentable"] = (df["accion"] == "seguir") & problema & df["motivo"].map(motivo_pide_id) & ~repetido
    df["estado_texto"] = df["estado"].map(ESTADOS_PEDIDO).fillna(df["estado"])
    df.loc[problema & repetido, "estado_texto"] = "Problema (se volvió a pedir)"
    return df
