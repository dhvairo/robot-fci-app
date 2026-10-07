"""Acceso a la base para la app: solo lectura, con resultados en caché por 10 minutos."""
import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

import calculos  # noqa: E402

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")


def _url():
    try:
        v = st.secrets["APP_DB_URL"]       # cuando la app está publicada
    except Exception:
        v = None
    return v or os.environ.get("APP_DB_URL")


def _ejecutar(sql, params=None):
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


@st.cache_data(ttl=600, show_spinner=False)
def consultar(sql, params=None):
    """Corre una consulta (solo lectura) y devuelve un DataFrame."""
    return _ejecutar(sql, params)


def fondos_seguidos():
    return consultar("select codigo_cnv, nombre, moneda, estado, id_ficha_web from fondos "
                     "where seguido order by nombre")


def fondos_ficha():
    """Fondos seguidos con todos los datos de la cabecera de la ficha (pedido 2.1 de octubre).

    patrimonio_neto = suma del patrimonio de sus clases con dato normal (sin marca de estado), en la moneda del fondo."""
    return consultar(
        "select f.codigo_cnv, f.nombre, f.moneda, f.estado, f.id_ficha_web, f.rubro, f.tipo_fondo, "
        "f.clasificacion_cnv, f.region, f.horizonte, f.descripcion_breve, f.tematica, f.tematica_provisoria, "
        "g.nombre as gerente, d.nombre as depositaria, "
        "(select sum(p.patrimonio) from foto_clases p where p.codigo_cnv = f.codigo_cnv "
        "and p.marca_estado = '') as patrimonio_neto, "
        "(select max(p.fecha_dato) from foto_clases p where p.codigo_cnv = f.codigo_cnv "
        "and p.marca_estado = '') as fecha_valores, "
        "(select max(x.fecha_cartera) from carteras_fechas x where x.codigo_cnv = f.codigo_cnv) as fecha_cartera "
        "from fondos f "
        "left join sociedades g on g.tipo = 'gerente' and g.codigo = f.codigo_gerente "
        "left join sociedades d on d.tipo = 'depositaria' and d.codigo = f.codigo_depositaria "
        "where f.seguido order by f.nombre")


def resumen_carteras():
    """Para el Resumen masivo: de la última cartera de cada fondo seguido, su fecha y patrimonio, la suma del % del
    PN por categoría resumen y sus 3 mayores tenencias. Devuelve (carteras, sumas, tenencias)."""
    carteras = consultar(
        "select x.codigo_cnv, x.fecha_cartera, x.patrimonio from carteras_fechas x "
        "join (select codigo_cnv, max(fecha_cartera) as fecha_cartera from carteras_fechas group by codigo_cnv) u "
        "using (codigo_cnv, fecha_cartera) join fondos f using (codigo_cnv) where f.seguido")
    sumas = consultar("select c.codigo_cnv, c.categoria_resumen, sum(c.pct_pn) as pct "
                      "from carteras_ultima c join fondos f using (codigo_cnv) where f.seguido "
                      "group by c.codigo_cnv, c.categoria_resumen")
    tenencias = consultar("select t.codigo_cnv, t.posicion, t.categoria_detallada, t.pct_pn from fondos_tenencias t "
                          "join fondos f using (codigo_cnv) where f.seguido order by t.codigo_cnv, t.posicion")
    return carteras, sumas, tenencias


def clases_gestion():
    """Todas las clases vigentes del catálogo con su foto diaria, costos y gerente (pantalla Gestión y eficiencia)."""
    return consultar(
        "select c.codigo_cafci, c.codigo_cnv, f.nombre as fondo, c.nombre as clase, g.nombre as gerente, "
        "f.rubro, p.tipo_fondo, p.moneda, p.patrimonio, p.calificacion, p.rend_12m, p.marca_estado, "
        "c.honorarios_sg, c.honorarios_sd, c.gastos_ordinarios, c.comision_ingreso, c.comision_rescate, "
        "c.comision_transferencia, c.honorarios_exito, p.fecha_dato "
        "from clases c join foto_clases p using (codigo_cafci) join fondos f on f.codigo_cnv = c.codigo_cnv "
        "left join sociedades g on g.tipo = 'gerente' and g.codigo = f.codigo_gerente "
        "where c.vigente_hasta is null order by f.nombre, c.nombre")


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


def historia_por_clase():
    """Primera y última fecha con cuotaparte numérica de cada clase (las filas que solo traen un estado no cuentan)."""
    return consultar("select codigo_cafci, min(fecha) as primera, max(fecha) as ultima "
                     "from valores_diarios_ultima where cuotaparte is not null group by codigo_cafci")


def resumen_base():
    r = consultar("select (select count(*) from fondos) as fondos, "
                  "(select count(*) from fondos where seguido) as seguidos, "
                  "(select count(*) from clases where vigente_hasta is null) as clases, "
                  "(select count(*) from valores_diarios_ultima) as valores, "
                  "(select max(fecha) from valores_diarios_ultima) as hasta, "
                  "(select count(*) from carteras_fechas) as carteras, "
                  "(select max(fecha_cartera) from carteras_fechas) as ultima_cartera, "
                  "(select count(*) from carteras where sin_clasificar) as sin_clasificar, "
                  "(select count(*) from hechos_relevantes) as hechos").iloc[0].copy()
    # Primera fecha con cuotaparte entre las clases activas (las suspendidas traen fechas de años atrás).
    r["desde"] = calculos.historia_desde(historia_por_clase())
    return r


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


# ───────── Sumar o quitar fondos ─────────

class ErrorPedido(Exception):
    """No se pudo anotar el pedido (el texto se muestra tal cual en la pantalla)."""


def _url_pedidos():
    try:
        v = st.secrets["PEDIDOS_DB_URL"]
    except Exception:
        v = None
    return v or os.environ.get("PEDIDOS_DB_URL")


@st.cache_data(ttl=60, show_spinner=False)
def catalogo_fondos():
    """Todos los fondos del catálogo (seguidos o no) para el buscador de "Sumar o quitar fondos"."""
    return _ejecutar(
        "select f.codigo_cnv, f.nombre, f.moneda, f.seguido, f.id_ficha_web, f.tipo_fondo, f.rubro, "
        "f.descripcion_breve, f.tematica, f.tematica_provisoria, g.nombre as gerente "
        "from fondos f left join sociedades g on g.tipo = 'gerente' and g.codigo = f.codigo_gerente "
        "order by f.nombre")


def pedidos_recientes(limite=60):
    """Los últimos pedidos con su estado. Sin caché: tiene que verse enseguida lo que se acaba de pedir."""
    return _ejecutar(
        "select p.id, p.codigo_cnv, f.nombre as fondo, p.accion, p.descripcion_breve, p.idea_tematica, "
        "p.id_ficha_cnv, p.estado, p.motivo, p.pedido_en, p.procesado_en from pedidos_fondos p "
        "left join fondos f using (codigo_cnv) order by p.id desc limit %s", (limite,))


def anotar_pedido(codigo_cnv, accion, descripcion=None, idea=None, id_ficha=None):
    """Anota un pedido en `pedidos_fondos` (lo único que puede escribir la app, con el usuario de permiso mínimo).
    Devuelve el número del pedido."""
    url = _url_pedidos()
    if not url:
        raise ErrorPedido("Falta PEDIDOS_DB_URL (archivo .env o secretos de la app): no se puede anotar el pedido.")
    try:
        with psycopg.connect(url, connect_timeout=20) as conn:
            fila = conn.execute(
                "insert into pedidos_fondos (codigo_cnv, accion, descripcion_breve, idea_tematica, id_ficha_cnv) "
                "values (%s, %s, %s, %s, %s) returning id",
                (int(codigo_cnv), accion, descripcion, idea, id_ficha)).fetchone()
    except psycopg.Error as e:
        raise ErrorPedido("No se pudo anotar el pedido: " + " ".join(str(e).replace(url, "***").split())[:160])
    return fila[0]
