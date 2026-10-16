"""Acceso a la base para la app: solo lectura, con resultados en caché por 10 minutos."""
import os
import json
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


class ErrorPreferencias(Exception):
    """Fallo seguro de configuración personal; no expone la conexión."""


def _personal(persona, sql, params=()):
    """Contexto de RLS local a la transacción, obtenido de Auth por acceso.py.

    Sin caché global: jamás mezcla organizaciones ni dueños.
    """
    try:
        with psycopg.connect(_url(), connect_timeout=20) as conn:
            conn.execute("select set_config('app.organizacion', %s, true), "
                         "set_config('app.dueno', %s, true)", (persona["organizacion"], persona["id"]))
            return conn.execute(sql, params).fetchall()
    except Exception:
        raise ErrorPreferencias("No se pudo acceder a la configuración personal.") from None


def organizacion(persona):
    columnas = ("id", "nombre", "logo", "color_principal", "color_secundario", "texto_legal")
    filas = _personal(persona, "select id,nombre,logo,color_principal,color_secundario,texto_legal "
                      "from public.organizaciones where id=%s", (persona["organizacion"],))
    if not filas:
        raise ErrorPreferencias("Organización no configurada.")
    return dict(zip(columnas, filas[0]))


def leer_preferencia(persona, clave):
    filas = _personal(persona, "select valor from public.preferencias "
                      "where organizacion=%s and dueno=%s and clave=%s",
                      (persona["organizacion"], persona["id"], clave))
    return filas[0][0] if filas else None


def guardar_preferencia(persona, clave, valor):
    from psycopg.types.json import Jsonb
    _personal(persona, "insert into public.preferencias(organizacion,dueno,clave,valor) "
              "values (%s,%s,%s,%s) on conflict (organizacion,dueno,clave) "
              "do update set valor=excluded.valor, actualizada=now() returning clave",
              (persona["organizacion"], persona["id"], clave, Jsonb(valor)))


CLASIFICACION_DESHABILITADA = "La clasificación por organización no está disponible."


def _contexto_clasificacion(persona=None, escritura=False):
    """Auth central revalidado; ninguna identidad procede de parámetros SQL del cliente."""
    import acceso
    if escritura:
        return acceso.requerir_admin_clasificacion()
    anterior = dict(acceso.usuario() or {})
    actual = acceso._validar_sesion()
    if (persona != actual or anterior.get("id") != actual.get("id")
            or anterior.get("organizacion") != actual.get("organizacion")
            or actual.get("cambio_obligatorio") or not actual.get("version_clave")
            or actual.get("rol") not in ("admin", "asesor")
            or actual.get("organizacion") != "propia"):
        raise acceso.ErrorAcceso("Usuario sin acceso autorizado.")
    return {"organizacion": actual["organizacion"], "dueno": "equipo",
            "usuario": actual["id"], "rol": actual["rol"]}


def _clasificacion_transaccion(contexto, operacion, solo_lectura=False):
    """Conexión propia, sin caché; commit al salir o rollback antes de informar fallo."""
    try:
        with psycopg.connect(_url(), connect_timeout=20) as conn:
            if solo_lectura:
                conn.execute('set transaction isolation level repeatable read, read only')
            conn.execute("select set_config('app.organizacion', %s, true), "
                         "set_config('app.dueno_colectivo', %s, true), "
                         "set_config('app.usuario', %s, true), "
                         "set_config('app.rol', %s, true)",
                         tuple(contexto[k] for k in ("organizacion", "dueno", "usuario", "rol")))
            resultado = operacion(conn)
        return resultado
    except Exception as error:
        if getattr(error, "sqlstate", None) in ("42P01", "42883", "3F000"):
            return {"estado": "deshabilitado", "mensaje": CLASIFICACION_DESHABILITADA}
        if getattr(error, "sqlstate", None) == "42501":
            raise calculos.ErrorClasificacion("Acceso a la clasificación no autorizado.") from None
        if getattr(error, "sqlstate", None) == "40001":
            return {"estado": "conflicto", "mensaje": "La clasificación cambió; recargá antes de guardar."}
        if isinstance(error, calculos.ErrorClasificacion):
            raise
        raise calculos.ErrorClasificacion("No se pudo acceder a la clasificación.") from None


def leer_clasificaciones(persona, ids):
    """Snapshot privado compartido por la organización; consultar jamás reconoce decisiones."""
    from uuid import UUID
    contexto = _contexto_clasificacion(persona)
    identificadores = [str(UUID(str(i))) for i in ids]

    return _clasificacion_transaccion(
        contexto, lambda conn: _leer_clasificaciones(conn, identificadores, contexto))


def _leer_clasificaciones(conn, identificadores, contexto):
    resultado = conn.execute("select public.clasificacion_leer(%s::uuid[])",
                             (identificadores,)).fetchone()[0]
    claves = {f["instrumento_id"]: json.dumps(f["componentes"], ensure_ascii=False,
              separators=(",", ":")) for f in resultado["fuentes"]}
    for regla in resultado["reglas"]:
        regla["instrumento"] = claves[regla["instrumento_id"]]
    resultado["decisiones"] = {
        (contexto["organizacion"], contexto["dueno"], claves[d["instrumento_id"]],
         tuple(d["huella"][:4])): d["accion"] for d in resultado["decisiones"]}
    return resultado


def componer_carteras_organizacion(filas_fuente):
    """U29: entrada servidor sin pantallas ni caché privada global.

    Recibe las carteras comunes ya cargadas (listas de dicts), no agregados ni
    organización/dueño del navegador. Solo lee registro y adaptador U26.
    """
    import acceso
    from psycopg.types.json import Jsonb

    contexto = _contexto_clasificacion(acceso.usuario())
    claves = set()
    for fila in filas_fuente:
        try:
            claves.add(calculos._componentes(fila))
        except calculos.ErrorClasificacion:
            pass  # El núcleo explica el bloqueo y conserva la fuente.

    def leer(conn):
        # Misma transacción/snapshot para resolver componentes y leer privadas.
        ids = [r[0] for r in conn.execute(
            'select id from public.instrumentos_cartera '
            'where clave_componentes = any(%s::jsonb[])',
            ([Jsonb(list(c)) for c in sorted(claves, key=str)],)).fetchall()]
        snapshot = _leer_clasificaciones(conn, ids, contexto)
        return calculos.componer_carteras(filas_fuente, snapshot, contexto)

    return _clasificacion_transaccion(contexto, leer, solo_lectura=True)


def vista_carteras():
    """Carga común compartida; composición privada nueva en cada ejecución autenticada.

    Solo consultar tiene caché global. La vista se pasa entre consumidores de una
    pantalla para que filtros, tablas y PDF usen exactamente el mismo snapshot.
    """
    fuente = consultar(
        "select c.*, c.rubro_cnv as rubro_cnv_original, "
        "c.instrumento as instrumento_original, h.moneda as moneda_del_fondo_original "
        "from carteras_ultima c join carteras_fechas h "
        "using (codigo_cnv, fecha_cartera) order by c.codigo_cnv, c.orden")
    resultado = componer_carteras_organizacion(
        fuente.astype(object).where(pd.notna(fuente), None).to_dict('records'))
    if resultado['estado'] == 'deshabilitado':
        st.caption(resultado['mensaje'])
    elif any(f['bloqueo'] for f in resultado['filas']):
        motivos = sorted({f['bloqueo'] for f in resultado['filas'] if f['bloqueo']})
        st.caption('Se conserva la fuente por identidad: ' + ', '.join(motivos) + '.')
    return resultado


def fondos_ficha(incluir_no_seguidos=False):
    comunes = _fondos_ficha_comunes(incluir_no_seguidos)
    vista = vista_carteras()
    salida = calculos.catalogo_efectivo(comunes, vista)
    salida.attrs['clasificacion'] = vista
    return salida


def resumen_carteras(vista=None):
    vista = vista_carteras() if vista is None else vista
    comunes = _resumen_carteras_comun() if vista['estado'] != 'habilitado' else None
    if comunes is not None:
        return comunes
    cabeceras = consultar(
        "select x.codigo_cnv, x.fecha_cartera, x.patrimonio from carteras_fechas x "
        "join (select codigo_cnv, max(fecha_cartera) as fecha_cartera from carteras_fechas group by codigo_cnv) u "
        "using (codigo_cnv, fecha_cartera) join fondos f using (codigo_cnv) where f.seguido")
    return calculos.resumenes_efectivos(cabeceras, vista)


def clases_gestion(vista=None):
    vista = vista_carteras() if vista is None else vista
    return calculos.catalogo_efectivo(_clases_gestion_comunes(), vista)


def cartera(codigo_cnv, vista=None):
    vista = vista_carteras() if vista is None else vista
    if vista['estado'] != 'habilitado':
        return _cartera_comun(codigo_cnv)
    columnas = ['orden', 'rubro_cnv', 'instrumento', 'pct_pn',
                *calculos.CAMPOS_CLASIFICACION, 'sin_clasificar']
    filas = [f for f in vista['filas'] if f['codigo_cnv'] == codigo_cnv]
    return calculos.filas_efectivas(filas).reindex(columns=columnas)


def cartera_encabezado(codigo_cnv, vista=None):
    vista = vista_carteras() if vista is None else vista
    return calculos.encabezado_efectivo(_cartera_encabezado_comun(codigo_cnv), vista, codigo_cnv)


def buscar_instrumentos(texto):
    vista = vista_carteras()
    if vista['estado'] != 'habilitado':
        return _buscar_instrumentos_comunes(texto)
    filas = calculos.filas_efectivas(vista['filas'])
    columnas = ['fondo', 'instrumento', 'pct_pn', 'categoria_resumen',
                'categoria_detallada', 'moneda', 'fecha_cartera']
    if filas.empty:
        return pd.DataFrame(columns=columnas)
    nombres = _fondos_ficha_comunes(True).set_index('codigo_cnv').nombre
    filas['fondo'] = filas.codigo_cnv.map(nombres)
    return filas[filas.instrumento.str.contains(texto, case=False, regex=False, na=False)].sort_values(
        'pct_pn', ascending=False, na_position='last')[columnas]


def _escribir_clasificacion(instrumento_id, accion, parche, version_regla, version_fuente):
    from uuid import UUID
    from psycopg.types.json import Jsonb
    contexto = _contexto_clasificacion(escritura=True)
    identificador = str(UUID(str(instrumento_id)))
    if (type(version_regla) is not int or version_regla < 0
            or type(version_fuente) is not int or version_fuente < 1):
        raise calculos.ErrorClasificacion("VERSION_INVALIDA")
    if accion == "guardar":
        # El contrato puro valida forma; SQL vuelve a validar opciones dentro del bloqueo.
        calculos._aplicar_parche({}, parche)
    elif accion not in ("aceptar", "conservar"):
        raise calculos.ErrorClasificacion("ACCION_INVALIDA")

    def escribir(conn):
        if accion == "guardar":
            return conn.execute("select public.clasificacion_guardar(%s::uuid, %s::jsonb, %s, %s)",
                                (identificador, Jsonb(parche), version_regla, version_fuente)).fetchone()[0]
        return conn.execute("select public.clasificacion_resolver(%s::uuid, %s, %s, %s)",
                            (identificador, accion, version_regla, version_fuente)).fetchone()[0]
    return _clasificacion_transaccion(contexto, escribir)


def guardar_clasificacion(instrumento_id, parche, version_regla, version_fuente):
    return _escribir_clasificacion(instrumento_id, "guardar", parche, version_regla, version_fuente)


def resolver_fuente(instrumento_id, accion, version_regla, version_fuente):
    return _escribir_clasificacion(instrumento_id, accion, None, version_regla, version_fuente)


@st.cache_data(ttl=600, show_spinner=False)
def consultar(sql, params=None):
    """Corre una consulta (solo lectura) y devuelve un DataFrame."""
    return _ejecutar(sql, params)


def fondos_seguidos():
    return consultar("select codigo_cnv, nombre, moneda, estado, id_ficha_web from fondos "
                     "where seguido order by nombre")


def _fondos_ficha_comunes(incluir_no_seguidos=False):
    """Cabecera y primera categoría de los seguidos; incluir_no_seguidos habilita todo el catálogo.

    patrimonio_neto = suma del patrimonio de sus clases con dato normal (sin marca de estado), en la moneda del fondo."""
    return consultar(
        "select f.codigo_cnv, f.nombre, f.moneda, f.estado, f.id_ficha_web, f.seguido, "
        "case when f.seguido then coalesce(nullif(f.rubro, ''), f.tipo_fondo) "
        "else coalesce(nullif(f.tipo_fondo, ''), 'Sin dato') end as rubro, f.tipo_fondo, "
        "case when f.seguido then coalesce(t.categoria_detallada, 'Sin cartera') "
        "else 'Sin cartera' end as categoria, "
        "f.clasificacion_cnv, f.region, f.horizonte, f.descripcion_breve, f.tematica, f.tematica_provisoria, "
        "g.nombre as gerente, d.nombre as depositaria, "
        "(select sum(p.patrimonio) from foto_clases p where p.codigo_cnv = f.codigo_cnv "
        "and p.marca_estado = '') as patrimonio_neto, "
        "(select max(p.fecha_dato) from foto_clases p where p.codigo_cnv = f.codigo_cnv "
        "and p.marca_estado = '') as fecha_valores, "
        "(select max(x.fecha_cartera) from carteras_fechas x where x.codigo_cnv = f.codigo_cnv) as fecha_cartera "
        "from fondos f left join fondos_tenencias t on t.codigo_cnv=f.codigo_cnv and t.posicion=1 "
        "left join sociedades g on g.tipo = 'gerente' and g.codigo = f.codigo_gerente "
        "left join sociedades d on d.tipo = 'depositaria' and d.codigo = f.codigo_depositaria "
        + ("" if incluir_no_seguidos else "where f.seguido ") + "order by f.nombre")


def _resumen_carteras_comun():
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


def _clases_gestion_comunes():
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


def foto_de_fondo(codigo_cnv):
    """Datos CAFCI y costos del catálogo; no requiere historia ni cartera."""
    return consultar(
        "select c.codigo_cafci, c.nombre, c.moneda, p.fecha_dato as fecha, "
        "case when p.marca_estado = '' then p.cuotaparte end as cuotaparte, "
        "case when p.marca_estado = '' then p.patrimonio end as patrimonio, "
        "case when p.marca_estado = '' then p.rend_mes end as mes, "
        "case when p.marca_estado = '' then p.rend_anio end as anio, "
        "case when p.marca_estado = '' then p.rend_12m end as meses_12, "
        "p.calificacion, p.marca_estado, c.honorarios_sg, c.honorarios_sd, c.gastos_ordinarios, "
        "c.comision_ingreso, c.comision_rescate, c.comision_transferencia, c.honorarios_exito "
        "from clases c left join foto_clases p using (codigo_cafci) "
        "where c.codigo_cnv=%s and c.vigente_hasta is null order by c.nombre", (codigo_cnv,))


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
                     "where codigo_cafci = any(%s) order by fecha",
                     (list(codigos_cafci),))


def rendimientos_ultimos(codigos_cafci):
    return consultar(
        "select distinct on (r.codigo_cafci) r.codigo_cafci, r.fecha, r.diario, r.dias_7, r.dias_30, "
        "r.mes, r.anio, r.meses_12, r.revisar, r.nota, v.cuotaparte, v.patrimonio "
        "from rendimientos r left join valores_diarios_ultima v using (codigo_cafci, fecha) "
        "where r.codigo_cafci = any(%s) order by r.codigo_cafci, r.fecha desc", (list(codigos_cafci),))


def _cartera_comun(codigo_cnv):
    return consultar("select c.orden, c.rubro_cnv, c.instrumento, c.pct_pn, c.categoria_resumen, "
                     "c.categoria_detallada, c.moneda, c.indexacion, c.sin_clasificar "
                     "from carteras_ultima c where c.codigo_cnv = %s order by c.orden", (codigo_cnv,))


def _cartera_encabezado_comun(codigo_cnv):
    return consultar("select fecha_cartera, moneda, patrimonio, instrumentos, sin_clasificar, "
                     "pct_sin_clasificar, controles_ok from carteras_fechas where codigo_cnv = %s "
                     "order by fecha_cartera desc limit 1", (codigo_cnv,))


def hechos(codigo_cnv):
    return consultar("select fecha, tipo, descripcion, link from hechos_relevantes "
                     "where codigo_cnv = %s order by fecha desc, documento desc", (codigo_cnv,))


def _buscar_instrumentos_comunes(texto):
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
    """Usuario para anotar pedidos: PEDIDOS_DB_URL si está (en la PC); si no, el de lectura (APP_DB_URL), que tiene
    el mismo permiso mínimo sobre pedidos_fondos (base_de_datos/usuario_app_anota_pedidos.sql). Así la app publicada
    no necesita un secreto nuevo en Streamlit."""
    try:
        v = st.secrets["PEDIDOS_DB_URL"]
    except Exception:
        v = None
    return v or os.environ.get("PEDIDOS_DB_URL") or _url()


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
        raise ErrorPedido("Falta PEDIDOS_DB_URL o APP_DB_URL (archivo .env o secretos de la app): no se puede anotar el pedido.")
    try:
        with psycopg.connect(url, connect_timeout=20) as conn:
            fila = conn.execute(
                "insert into pedidos_fondos (codigo_cnv, accion, descripcion_breve, idea_tematica, id_ficha_cnv) "
                "values (%s, %s, %s, %s, %s) returning id",
                (int(codigo_cnv), accion, descripcion, idea, id_ficha)).fetchone()
    except psycopg.Error as e:
        raise ErrorPedido("No se pudo anotar el pedido: " + " ".join(str(e).replace(url, "***").split())[:160])
    return fila[0]
