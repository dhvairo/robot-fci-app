"""Acceso a la base para la app: solo lectura, con resultados en caché por 10 minutos."""
import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")


def _url():
    try:
        v = st.secrets["APP_DB_URL"]       # cuando la app está publicada
    except Exception:
        v = None
    return v or os.environ.get("APP_DB_URL")


@st.cache_data(ttl=600, show_spinner=False)
def consultar(sql, params=None):
    """Corre una consulta (solo lectura) y devuelve un DataFrame."""
    url = _url()
    if not url:
        st.error("Falta APP_DB_URL (archivo .env o secretos de la app).")
        st.stop()
    with psycopg.connect(url, connect_timeout=20) as conn:
        cur = conn.execute(sql, params)
        columnas = [c.name for c in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columnas)
    for c in df.columns:
        if df[c].map(lambda x: isinstance(x, Decimal)).any():
            df[c] = df[c].astype(float)
    return df


def fondos_seguidos():
    return consultar("select codigo_cnv, nombre, moneda, estado, id_ficha_web from fondos "
                     "where seguido order by nombre")


def clases_de_fondo(codigo_cnv):
    return consultar("select codigo_cafci, nombre, moneda from clases "
                     "where codigo_cnv = %s and vigente_hasta is null order by nombre", (codigo_cnv,))


def clases_seguidas():
    return consultar("select c.codigo_cafci, c.codigo_cnv, f.nombre as fondo, c.nombre as clase, "
                     "c.moneda from clases c join fondos f using (codigo_cnv) "
                     "where f.seguido and c.vigente_hasta is null order by f.nombre, c.nombre")


def clases_preferidas(df_clases):
    """Clase A y B de cada fondo; si no existen, las que haya (regla de la especificación)."""
    ab = df_clases[df_clases["nombre"].str.contains(r"Clase [AB]\b", regex=True)]
    return ab if len(ab) else df_clases


def serie(codigos_cafci):
    return consultar("select codigo_cafci, fecha, cuotaparte, patrimonio from valores_diarios_ultima "
                     "where codigo_cafci = any(%s) and cuotaparte is not null order by fecha",
                     (list(codigos_cafci),))


def rendimientos_ultimos(codigos_cafci):
    return consultar(
        "select distinct on (r.codigo_cafci) r.codigo_cafci, r.fecha, r.diario, r.dias_7, r.dias_30, "
        "r.mes, r.anio, r.meses_12, r.revisar, r.nota, v.cuotaparte, v.patrimonio "
        "from rendimientos r left join valores_diarios_ultima v using (codigo_cafci, fecha) "
        "where r.codigo_cafci = any(%s) order by r.codigo_cafci, r.fecha desc", (list(codigos_cafci),))


def cartera(codigo_cnv):
    return consultar("select c.orden, c.rubro_cnv, c.instrumento, c.pct_pn, c.categoria_resumen, "
                     "c.categoria_detallada, c.moneda, c.indexacion, c.sin_clasificar "
                     "from carteras_ultima c where c.codigo_cnv = %s order by c.orden", (codigo_cnv,))


def cartera_encabezado(codigo_cnv):
    return consultar("select fecha_cartera, moneda, patrimonio, instrumentos, sin_clasificar, "
                     "pct_sin_clasificar, controles_ok from carteras_fechas where codigo_cnv = %s "
                     "order by fecha_cartera desc limit 1", (codigo_cnv,))


def hechos(codigo_cnv):
    return consultar("select fecha, tipo, descripcion, link from hechos_relevantes "
                     "where codigo_cnv = %s order by fecha desc, documento desc", (codigo_cnv,))


def buscar_instrumentos(texto):
    return consultar("select f.nombre as fondo, c.instrumento, c.pct_pn, c.categoria_resumen, "
                     "c.categoria_detallada, c.moneda, c.fecha_cartera "
                     "from carteras_ultima c join fondos f using (codigo_cnv) "
                     "where c.instrumento ilike %s order by c.pct_pn desc nulls last",
                     (f"%{texto}%",))


def estado_robot():
    return consultar("select fuente, nombre_archivo, fecha_dato, version, filas, controles_ok, "
                     "recibido_en from archivos_procesados order by recibido_en desc limit 20")


def resumen_base():
    return consultar("select (select count(*) from fondos) as fondos, "
                     "(select count(*) from fondos where seguido) as seguidos, "
                     "(select count(*) from clases where vigente_hasta is null) as clases, "
                     "(select count(*) from valores_diarios_ultima) as valores, "
                     # Primera fecha entre las clases activas (las suspendidas traen fechas de años atrás).
                     "(select min(fecha) from valores_diarios_ultima where codigo_cafci in ("
                     "select codigo_cafci from valores_diarios_ultima group by codigo_cafci "
                     "having max(fecha) >= (select max(fecha) from valores_diarios_ultima) - 10)) as desde, "
                     "(select max(fecha) from valores_diarios_ultima) as hasta, "
                     "(select count(*) from carteras_fechas) as carteras, "
                     "(select max(fecha_cartera) from carteras_fechas) as ultima_cartera, "
                     "(select count(*) from carteras where sin_clasificar) as sin_clasificar, "
                     "(select count(*) from hechos_relevantes) as hechos").iloc[0]


def ultimas_entradas():
    """Última vez que entró un dato nuevo de cada fuente (momentos con zona horaria)."""
    return consultar(
        "select (select max(recibido_en) from archivos_procesados where fuente = 'CAFCI' and version = 1) as noche, "
        "(select max(recibido_en) from archivos_procesados where fuente = 'CAFCI' and version > 1) as tarde, "
        "(select max(recibido_en) from archivos_procesados where fuente = 'CNV') as carteras, "
        "(select max(actualizado_en) from foto_clases) as foto").iloc[0]


def filas_a_revisar(dias=120):
    """Rendimientos marcados "a revisar" (difieren de la CAFCI en más de 0,01 punto) de los últimos `dias` días."""
    return consultar(
        "select f.nombre as fondo, c.nombre as clase, r.fecha, r.diario, v.variacion_diaria_cafci, "
        "r.diferencia_vs_cafci, r.nota from rendimientos r "
        "join valores_diarios_ultima v using (codigo_cafci, fecha) "
        "join clases c on c.codigo_cafci = r.codigo_cafci and c.vigente_hasta is null "
        "join fondos f using (codigo_cnv) "
        "where r.revisar and r.fecha >= (select max(fecha) from rendimientos) - %s "
        "order by r.fecha desc, f.nombre, c.nombre", (dias,))
