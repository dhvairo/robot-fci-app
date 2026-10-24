"""Acceso a la base: comunes por versión; privados solo en sesión."""
import os
import json
import hashlib
import time
from concurrent.futures import Future
from contextlib import contextmanager
from threading import Lock, RLock
from queue import Queue
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

import calculos  # noqa: E402

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")


class _ConexionCompartida:
    """Una conexión prestada a un único consumidor; GUC solo en transacciones."""
    def __init__(self):
        self.conn = None


@st.cache_resource(show_spinner=False)
def _recurso_conexion(args, opciones):
    pool = Queue()
    for _ in range(6):
        pool.put(_ConexionCompartida())
    return pool


def _url():
    try:
        v = st.secrets["APP_DB_URL"]
    except Exception:
        v = None
    return v or os.environ.get("APP_DB_URL")


def _pool_comun(url):
    return _recurso_conexion((url,), (('connect_timeout', 20),))


_pool_comun.clear = _recurso_conexion.clear


def preparar_conexiones():
    """Solo arranque/monitor: abrir y comprobar recursos, nunca desde una pantalla."""
    import acceso
    configuraciones = [((_url(),), {'connect_timeout': 20})]
    pedidos = acceso.secreto('PEDIDOS_DB_URL')
    if pedidos:
        configuraciones.append(((pedidos,), {'connect_timeout': 20}))
    c = acceso.secreto('seguimiento_privado')
    if c and c.get('activa') and c.get('host'):
        configuraciones.append(((), {k: c[k] for k in
            ('host', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout')}))
    def preparar(args, opciones):
        pool = _recurso_conexion(args, tuple(sorted(opciones.items())))
        recursos = [pool.get(timeout=60) for _ in range(6)]
        def vivo(recurso):
            try:
                if recurso.conn is not None and not recurso.conn.closed and not recurso.conn.broken:
                    try:
                        recurso.conn.execute('select 1')
                        return
                    except Exception:
                        recurso.conn.close()
                recurso.conn = psycopg.connect(*args, **opciones, autocommit=True)
            finally:
                pool.put(recurso)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as hilos:
            list(hilos.map(vivo, recursos))
    for args, opciones in configuraciones:
        if args and not args[0]:
            continue
        preparar(args, opciones)


class _Prestamo:
    """DB-API para preferencias: close devuelve al pool después de rollback."""
    def __init__(self, pool, recurso):
        self.pool, self.recurso = pool, recurso

    def __getattr__(self, nombre):
        return getattr(self.recurso.conn, nombre)

    def close(self):
        if self.recurso is not None:
            recurso, self.recurso = self.recurso, None
            try:
                recurso.conn.rollback()
            finally:
                self.pool.put(recurso)


def prestar_conexion(*args, **opciones):
    pool = _recurso_conexion(args, tuple(sorted(opciones.items())))
    recurso = pool.get(timeout=60)
    if recurso.conn is None or recurso.conn.closed or recurso.conn.broken:
        pool.put(recurso)
        raise ErrorPreferencias('La conexión inicial está pendiente. Volvé a intentar.')
    return _Prestamo(pool, recurso)


@contextmanager
def conexion_compartida(*args, **opciones):
    import acceso
    from streamlit.runtime import exists
    if acceso._estado_rerun() is None and not exists():
        with psycopg.connect(*args, **opciones) as conn:
            yield conn
        return
    prestamo = prestar_conexion(*args, **opciones)
    try:
        with prestamo.recurso.conn.transaction():
            yield prestamo.recurso.conn
    finally:
        prestamo.close()


@contextmanager
def _conexion_comun(url):
    prestamo = prestar_conexion(url, connect_timeout=20)
    try:
        with prestamo.recurso.conn.transaction():
            yield prestamo.recurso.conn
    finally:
        prestamo.close()


TOPE_LECTURA_DIA = 150_000_000
_lectura = {'dia': None, 'bytes': 0}
_lock_lectura = RLock()


class TopeLectura(Exception):
    """El proceso ya leyó de Supabase lo presupuestado para hoy."""


def mb_leidos_hoy():
    """MB (estimados por el tamaño de los resultados) que este proceso leyó hoy de Supabase."""
    with _lock_lectura:
        return _lectura['bytes'] / 1_000_000 if _lectura['dia'] == time.strftime('%Y-%m-%d') else 0.0


def _presupuesto_lectura(nuevos=0):
    """Suma `nuevos` bytes al día; con el tope superado no deja pasar otra lectura de la base."""
    hoy = time.strftime('%Y-%m-%d')
    with _lock_lectura:
        if _lectura['dia'] != hoy:
            _lectura.update(dia=hoy, bytes=0)
        if _lectura['bytes'] >= TOPE_LECTURA_DIA:
            raise TopeLectura()
        _lectura['bytes'] += nuevos
        total = _lectura['bytes']
    if nuevos:
        import logging
        logging.getLogger('seguimiento').log(logging.WARNING, 'SEGUIMIENTO LECTURA %.3f_MB día %.3f_MB',
                                            nuevos / 1_000_000, total / 1_000_000)


def _ejecutar(sql, params=None):
    url = _url()
    if not url:
        st.error("Falta APP_DB_URL (archivo .env o secretos de la app).")
        st.stop()
    _presupuesto_lectura()
    with _conexion_comun(url) as conn, calculos.medir_seguimiento('CONSULTA_COMUN'):
        cur = conn.execute(sql, params)
        columnas = [c.name for c in cur.description]
        numericas = [c.name for c in cur.description if getattr(c, 'type_code', None) == 1700]
        filas = cur.fetchall()
    with calculos.medir_seguimiento('COMUN_DATAFRAME'):
        df = pd.DataFrame.from_records(filas, columns=columnas)
    with calculos.medir_seguimiento('COMUN_BYTES'):
        _presupuesto_lectura(int(df.memory_usage(index=False, deep=True).sum()))
    with calculos.medir_seguimiento('COMUN_TIPOS'):
        # PostgreSQL ya informa NUMERIC: evitar un callback Python por celda/columna.
        for c in numericas:
            df[c] = df[c].astype(float)
    return df


class ErrorPreferencias(Exception):
    """Fallo seguro de configuración personal; no expone la conexión."""


def _personal(persona, sql, params=()):
    """Contexto de RLS local a la transacción, obtenido de Auth por acceso.py.

    Sin caché global: jamás mezcla organizaciones ni dueños.
    """
    try:
        with conexion_compartida(_url(), connect_timeout=20) as conn, calculos.medir_seguimiento('CONSULTA_PERSONAL'):
            conn.execute("select set_config('app.organizacion', %s, true), "
                         "set_config('app.dueno', %s, true)", (persona["organizacion"], persona["id"]))
            return conn.execute(sql, params).fetchall()
    except Exception:
        raise ErrorPreferencias("No se pudo acceder a la configuración personal.") from None


def organizacion(persona):
    import acceso
    return acceso.lectura_privada('marca', persona, lambda: dict(_marca_organizacion(
        persona['organizacion'], _persona=persona)[1]))


@st.cache_resource(ttl=300, max_entries=128, show_spinner=False)
def _marca_organizacion(organizacion, _persona):
    """Marca común por organización; sello del contenido leído bajo RLS, no del usuario.

    Se valida como máximo al vencer los mismos cinco minutos de la lectura anterior.
    El primer Auth precalienta; no se consulta una organización antes de autorizarla.
    """
    valor = _organizacion_sin_cache(dict(_persona, organizacion=organizacion))
    sello = hashlib.sha256(json.dumps(valor, sort_keys=True).encode('utf-8')).hexdigest()
    return sello, _marca_por_sello(organizacion, sello, _valor=valor)


@st.cache_resource(max_entries=128, show_spinner=False)
def _marca_por_sello(organizacion, sello, _valor):
    return dict(_valor)


def _organizacion_sin_cache(persona):
    columnas = ("id", "nombre", "logo", "color_principal", "color_secundario", "texto_legal")
    filas = _personal(persona, "select id,nombre,logo,color_principal,color_secundario,texto_legal "
                      "from public.organizaciones where id=%s", (persona["organizacion"],))
    if not filas:
        raise ErrorPreferencias("Organización no configurada.")
    return dict(zip(columnas, filas[0]))


def leer_preferencia(persona, clave):
    import acceso
    return acceso.lectura_privada(('preferencia', clave), persona,
                                 lambda: _leer_preferencia_sin_cache(persona, clave))


def _leer_preferencia_sin_cache(persona, clave):
    filas = _personal(persona, "select valor from public.preferencias "
                      "where organizacion=%s and dueno=%s and clave=%s",
                      (persona["organizacion"], persona["id"], clave))
    return filas[0][0] if filas else None


def guardar_preferencia(persona, clave, valor):
    import acceso
    if acceso._estado_rerun() is not None:
        persona = acceso.revalidar_escritura()
    from psycopg.types.json import Jsonb
    _personal(persona, "insert into public.preferencias(organizacion,dueno,clave,valor) "
              "values (%s,%s,%s,%s) on conflict (organizacion,dueno,clave) "
              "do update set valor=excluded.valor, actualizada=now() returning clave",
              (persona["organizacion"], persona["id"], clave, Jsonb(valor)))
    acceso.invalidar_lecturas_privadas()


CLASIFICACION_DESHABILITADA = "La clasificación por organización no está disponible."


class RepositorioSeguimiento:
    """U40, desconectado: registro y conector explícitos del servidor, nunca formulario.

    Sin URL predeterminada; lectura corta por sesión y contexto revalidado.
    El conector de la app reutiliza exclusivamente su rol acotado.
    """
    def __init__(self, registro=None, conectar=None):
        self.registro = dict(registro or {})
        self.conectar = conectar

    def _operar(self, par, funcion, *, _contexto=None):
        import acceso
        motivo = 'CONTEXTO'
        try:
            # El precalentado recibe la copia validada en el hilo de ingreso.
            contexto = (acceso.requerir_contexto_seguimiento(self.registro)
                        if _contexto is None else dict(_contexto))
            motivo = 'PAR'
            if tuple(par) != (contexto['organizacion'], contexto['dueno']):
                raise calculos.ErrorSeguimiento('DUENO_AJENO')
            if self.conectar is None:
                raise calculos.ErrorSeguimiento('PERSISTENCIA_NO_CONFIGURADA')
            motivo = 'CONEXION'
            with self.conectar() as conn:
                # SET LOCAL y la lectura siempre comparten transacción, aun con autocommit.
                from contextlib import nullcontext
                from psycopg.pq import TransactionStatus
                iniciar = getattr(conn, 'autocommit', False) and conn.info.transaction_status == TransactionStatus.IDLE
                with conn.transaction() if iniciar else nullcontext():
                    motivo = 'GUC'
                    conn.execute("select set_config('seguimiento.organizacion', %s, true), "
                                 "set_config('seguimiento.dueno', %s, true), "
                                 "set_config('seguimiento.usuario', %s, true), "
                                 "set_config('seguimiento.rol', %s, true), "
                                 "set_config('seguimiento.version_clave', %s, true)",
                                 tuple(contexto[k] for k in
                                       ('organizacion', 'dueno', 'usuario', 'rol', 'version_clave')))
                    motivo = 'OPERACION'
                    with calculos.medir_seguimiento('CONSULTA_SEGUIMIENTO'):
                        resultado = funcion(conn, contexto)
                    motivo = 'COMMIT'
            return resultado  # Solo después del commit, incluso si este falla.
        except calculos.ErrorSeguimiento as error:
            calculos.registrar_fallo_seguimiento('PERSISTENCIA', motivo, error)
            raise
        except Exception as error:
            calculos.registrar_fallo_seguimiento('PERSISTENCIA', motivo, error)
            raise calculos.ErrorSeguimiento('PERSISTENCIA_FALLIDA') from None

    @staticmethod
    def _leer(conn, contexto):
        estado = conn.execute('select seguimiento_privado.leer(%s, %s)',
                              (contexto['organizacion'], contexto['dueno'])).fetchone()[0]
        return calculos._filas_seguimiento(estado, contexto)

    def leer(self, par):
        import acceso
        try:
            contexto = acceso.requerir_contexto_seguimiento(self.registro)
            if tuple(par) != (contexto['organizacion'], contexto['dueno']):
                raise calculos.ErrorSeguimiento('DUENO_AJENO')
            clave = getattr(self, 'clave_cache', id(self))
            def cargar():
                preparada = st.session_state.get('_arranque_seguimiento')
                if preparada and preparada[:2] == (clave, contexto):
                    import rapidez
                    rapidez._esperar(preparada[2])
                    return preparada[2].result()
                return self._operar(par, self._leer)
            return acceso.lectura_privada(('seguimiento', clave), contexto, cargar)
        except calculos.ErrorSeguimiento:
            raise
        except Exception as error:
            calculos.registrar_fallo_seguimiento('PERSISTENCIA', 'CONTEXTO', error)
            raise calculos.ErrorSeguimiento('PERSISTENCIA_FALLIDA') from None

    def transaccion(self, par, funcion):
        from copy import deepcopy
        from psycopg.types.json import Jsonb

        def anotar(conn, contexto):
            conn.execute('select seguimiento_privado.bloquear(%s, %s)', tuple(par))
            anterior = self._leer(conn, contexto)
            estado = deepcopy(anterior)
            resultado = funcion(estado)
            # El protocolo U37 anota un único pedido; no admite escrituras arbitrarias.
            nuevos = estado['pedidos'][len(anterior['pedidos']):]
            if (estado['elecciones'] != anterior['elecciones']
                    or estado['textos'] != anterior['textos']
                    or estado['pedidos'][:len(anterior['pedidos'])] != anterior['pedidos']
                    or len(nuevos) > 1):
                raise calculos.ErrorSeguimiento('TRANSICION_INVALIDA')
            if nuevos:
                guardado = conn.execute('select seguimiento_privado.solicitar(%s, %s, %s::jsonb)',
                                        (*par, Jsonb(nuevos[0]))).fetchone()[0]
                if guardado != resultado:
                    raise calculos.ErrorSeguimiento('RESPUESTA_INVALIDA')
            elif resultado not in anterior['pedidos']:
                raise calculos.ErrorSeguimiento('TRANSICION_INVALIDA')
            return resultado

        import acceso
        try:
            acceso.revalidar_escritura()
            return self._operar(par, anotar)
        finally:
            acceso.invalidar_lecturas_privadas()

    def confirmar(self, par, clave, preparado=False):
        """Entrada reservada al futuro operador SQL, sin llamadas desde pantallas."""
        if not isinstance(clave, str) or not clave.strip() or type(preparado) is not bool:
            raise calculos.ErrorSeguimiento('CONFIRMACION_INVALIDA')
        def confirmar(conn, contexto):
            estado = conn.execute('select seguimiento_privado.confirmar(%s, %s, %s, %s)',
                                  (*par, clave, preparado)).fetchone()[0]
            calculos._filas_seguimiento(estado, contexto)
            if not any(p['clave'] == clave and p['estado'] == 'listo' for p in estado['pedidos']):
                raise calculos.ErrorSeguimiento('CONFIRMACION_INVALIDA')
            return estado

        import acceso
        try:
            acceso.revalidar_escritura()
            return self._operar(par, confirmar)
        finally:
            acceso.invalidar_lecturas_privadas()


def _contexto_clasificacion(persona=None, escritura=False):
    """Auth central revalidado; ninguna identidad procede de parámetros SQL del cliente."""
    import acceso
    if escritura:
        return acceso.requerir_admin_clasificacion()
    if acceso.roles_activos():
        c = acceso.configuracion_seguimiento()
        return acceso.requerir_contexto_seguimiento(c.get('registro'))
    anterior = dict(acceso.usuario() or {})
    actual = acceso._validar_sesion()
    if (persona != actual or anterior.get("id") != actual.get("id")
            or anterior.get("organizacion") != actual.get("organizacion")
            or actual.get("cambio_obligatorio") or not actual.get("version_clave")
            or actual.get("rol") not in (('admin', 'asesor', 'cliente') if acceso.roles_activos()
                                        else ('admin', 'asesor'))
            or actual.get("organizacion") != "propia"):
        raise acceso.ErrorAcceso("Usuario sin acceso autorizado.")
    return {"organizacion": actual["organizacion"], "dueno": "equipo",
            "usuario": actual["id"], "rol": actual["rol"]}


def _clasificacion_transaccion(contexto, operacion, solo_lectura=False):
    """Transacción propia, conexión reutilizable; rollback antes de informar fallo."""
    try:
        with conexion_compartida(_url(), connect_timeout=20) as conn:
            if solo_lectura:
                conn.execute('set transaction isolation level repeatable read, read only')
            conn.execute("select set_config('app.organizacion', %s, true), "
                         "set_config('app.dueno_colectivo', %s, true), "
                         "set_config('app.usuario', %s, true), "
                         "set_config('app.rol', %s, true)",
                         tuple(contexto[k] for k in ("organizacion", "dueno", "usuario", "rol")))
            with calculos.medir_seguimiento('CONSULTA_CLASIFICACION'):
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

    import acceso
    return acceso.lectura_privada(('clasificaciones', tuple(identificadores)), contexto,
        lambda: _clasificacion_transaccion(
            contexto, lambda conn: _leer_clasificaciones(conn, identificadores, contexto)))


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


def componer_carteras_organizacion(filas_fuente, *, contexto_validado=None):
    """U29: entrada servidor sin pantallas ni caché privada global.

    Recibe las carteras comunes ya cargadas (listas de dicts), no agregados ni
    organización/dueño del navegador. Solo lee registro y adaptador U26.
    """
    return _componer_con_sello(filas_fuente, contexto_validado, False)[0]


# Sello liviano de lo privado y de la identidad, bajo la misma RLS del contexto: si no cambia, la vista guardada
# sigue vigente y no se baja el JSON grande de clasificacion_leer.
SELLO_CLASIFICACION_SQL = (
    "select jsonb_build_array("
    "(select jsonb_build_array(count(*), coalesce(sum(version), 0), max(editado)) "
    "from public.clasificaciones_organizacion), "
    "(select jsonb_build_array(count(*), max(editado)) from public.decisiones_fuente_organizacion), "
    "(select jsonb_build_array(count(*), coalesce(sum(version_fuente), 0), count(*) filter (where ambiguo)) "
    "from public.instrumentos_cartera), "
    "(select jsonb_build_array(count(*), max(recibido)) from public.clasificacion_fuente_versiones))")


def _componer_con_sello(filas_fuente, contexto_validado, con_sello):
    """Devuelve (vista, sello); el sello se lee en la misma transacción que los datos."""
    import acceso
    from psycopg.types.json import Jsonb

    contexto = (dict(contexto_validado) if contexto_validado is not None
                else _contexto_clasificacion(acceso.usuario()))
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
        sello = conn.execute(SELLO_CLASIFICACION_SQL).fetchone()[0] if con_sello else None
        snapshot = _leer_clasificaciones(conn, ids, contexto)
        return calculos.componer_carteras(filas_fuente, snapshot, contexto), sello

    resultado = _clasificacion_transaccion(contexto, leer, solo_lectura=True)
    if isinstance(resultado, dict):  # tabla ausente: estado «deshabilitado», sin sello
        return resultado, None
    return resultado


def vista_carteras():
    import acceso
    contexto = _contexto_clasificacion(acceso.usuario())
    return acceso.lectura_privada(('vista_carteras', version_comun()), contexto,
                                 _vista_carteras_sin_cache, copiar=False)


def _vista_carteras_sin_cache():
    """Carga común compartida; composición compartida por organización (ver _cargar_vista_carteras).

    La vista se pasa entre consumidores de una pantalla para que filtros, tablas y PDF
    usen exactamente el mismo snapshot.
    """
    import rapidez
    resultado = rapidez.vista_inicial()
    if resultado is None:
        resultado = _cargar_vista_carteras()
    if resultado['estado'] == 'deshabilitado':
        st.caption(resultado['mensaje'])
    elif any(f['bloqueo'] for f in resultado['filas']):
        motivos = sorted({f['bloqueo'] for f in resultado['filas'] if f['bloqueo']})
        st.caption('Se conserva la fuente por identidad: ' + ', '.join(motivos) + '.')
    return resultado


_VISTAS_ORGANIZACION = globals().get('_VISTAS_ORGANIZACION', {})
_CERROJO_VISTAS = globals().get('_CERROJO_VISTAS', Lock())
_COLUMNAS_HUELLA = ('codigo_cnv', 'fecha_cartera', 'orden', 'instrumento', 'pct_pn', 'rubro_cnv',
                    'categoria_resumen', 'categoria_detallada', 'moneda', 'indexacion', 'sin_clasificar')


def _huella_comun(fuente):
    """Versión de los datos comunes ya cargados; None si no se puede huellar (no se comparte)."""
    try:
        columnas = [c for c in _COLUMNAS_HUELLA if c in fuente.columns]
        return (len(fuente), tuple(columnas),
                int(pd.util.hash_pandas_object(fuente[columnas], index=False).sum()))
    except Exception:
        return None


def _sello_privado(contexto):
    """Una consulta liviana bajo la misma RLS; None si no se pudo (entonces no se comparte)."""
    try:
        resultado = _clasificacion_transaccion(
            contexto, lambda conn: conn.execute(SELLO_CLASIFICACION_SQL).fetchone()[0], solo_lectura=True)
    except calculos.ErrorClasificacion:
        return None
    return None if isinstance(resultado, dict) else json.dumps(resultado, sort_keys=True, default=str)


def invalidar_vista_organizacion(contexto):
    """Guardar una clasificación descarta lo compartido de esa organización y dueño."""
    with _CERROJO_VISTAS:
        _VISTAS_ORGANIZACION.pop((contexto.get('organizacion'), contexto.get('dueno')), None)


def _cargar_vista_carteras(contexto_validado=None):
    """Vista de carteras compartida por organización, después de validar Auth.

    Sustituye la regla «jamás caché global» solo para esta vista: su contenido depende únicamente de
    (organización, dueño) y de los datos comunes, nunca de la persona (admin y asesor ven lo mismo), y el
    contexto llega ya validado por _contexto_clasificacion. La llave incluye el sello de versión; si no
    cambió se sirve lo guardado sin bajar clasificacion_leer. Un único cálculo a la vez por organización.
    """
    import acceso
    contexto = (dict(contexto_validado) if contexto_validado is not None
                else _contexto_clasificacion(acceso.usuario()))
    fuente = _fuente_carteras_comun()
    huella = _huella_comun(fuente)
    if huella is None or contexto.get('rol') not in ('admin', 'asesor'):
        return _calcular_vista(fuente, contexto, False)[0]
    llave = (contexto['organizacion'], contexto['dueno'])
    sello_previo = (huella, _sello_privado(contexto))
    if sello_previo[1] is None:
        return _calcular_vista(fuente, contexto, False)[0]
    with _CERROJO_VISTAS:
        registro = _VISTAS_ORGANIZACION.get(llave)
        if registro and registro['sello'] == sello_previo:
            return registro['vista']
        tarea = registro['tarea'] if registro and registro['tarea_sello'] == sello_previo else None
        if tarea is None:
            tarea, propia = Future(), True
            _VISTAS_ORGANIZACION[llave] = {**(registro or {'sello': None, 'vista': None}),
                                           'tarea': tarea, 'tarea_sello': sello_previo}
        else:
            propia = False
    if not propia:
        import rapidez
        return tarea.result(timeout=rapidez.TIEMPO_ESPERA)
    try:
        vista, sello = _calcular_vista(fuente, contexto, True)
        with _CERROJO_VISTAS:
            actual = _VISTAS_ORGANIZACION.get(llave)
            if actual and actual.get('tarea') is tarea:
                guardable = sello is not None and vista.get('estado') == 'habilitado'
                _VISTAS_ORGANIZACION[llave] = {
                    'sello': (huella, json.dumps(sello, sort_keys=True, default=str)) if guardable else None,
                    'vista': vista if guardable else None, 'tarea': None, 'tarea_sello': None}
        tarea.set_result(vista)
        return vista
    except BaseException as error:
        with _CERROJO_VISTAS:
            actual = _VISTAS_ORGANIZACION.get(llave)
            if actual and actual.get('tarea') is tarea:
                actual['tarea'] = actual['tarea_sello'] = None
        tarea.set_exception(error)
        raise


def _calcular_vista(fuente, contexto, con_sello):
    filas = fuente.astype(object).where(pd.notna(fuente), None).to_dict('records')
    return _componer_con_sello(filas, contexto, con_sello)


def _fuente_carteras_comun():
    """Fuente pública de la vista; la composición privada siempre queda fuera."""
    return consultar(
        "select c.*, c.rubro_cnv as rubro_cnv_original, "
        "c.instrumento as instrumento_original, h.moneda as moneda_del_fondo_original "
        "from carteras_ultima c join carteras_fechas h "
        "using (codigo_cnv, fecha_cartera) order by c.codigo_cnv, c.orden")


class VistaCompartida(dict):
    """Vista de carteras adjunta a un DataFrame: pandas la copia en cada operación; esta no se copia."""

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def fondos_ficha(incluir_no_seguidos=False):
    import rapidez
    try:
        privada = calculos.lectura_seguimiento_activa()
    except calculos.ErrorSeguimiento:
        st.error('No se pudo leer el seguimiento de tu organización. Consulta rechazada.')
        st.stop()
        raise
    def componer():
        incluir = True if privada else incluir_no_seguidos
        tarea_ficha = st.session_state.get('_arranque_ficha') if incluir else None
        if tarea_ficha is not None and st.session_state.get('_arranque_privado'):
            rapidez.vista_inicial()  # Revalidar identidad/versión antes de usar el resultado.
            tarea_ficha = st.session_state['_arranque_ficha']
            rapidez._esperar(tarea_ficha)
            catalogo, vista = tarea_ficha.result()
            salida = catalogo.copy()
        else:
            comunes = rapidez.leer_comun('FONDOS_' + str(bool(incluir)), lambda: _fondos_ficha_comunes(incluir))
            vista = vista_carteras()
            salida = calculos.catalogo_efectivo(comunes, vista)
        salida = _seguimiento_para_consulta(salida)
        if privada and not incluir_no_seguidos:
            salida = salida[salida['seguido']].copy()
        salida.attrs['clasificacion'] = VistaCompartida(vista)
        return salida
    import acceso
    # Con seguimiento privado el Auth se revalida en cada clic; apagado conserva el camino legado (sin Auth).
    contexto = _contexto_clasificacion(acceso.usuario()) if privada else None
    return rapidez.memorizar('fondos_ficha', componer, bool(incluir_no_seguidos), bool(privada),
                             contexto=contexto).copy()


def _seguimiento_para_consulta(catalogo):
    try:
        return calculos.seguimiento_para_consulta(catalogo)
    except calculos.ErrorSeguimiento:
        st.error('No se pudo leer el seguimiento de tu organización. Consulta rechazada.')
        st.stop()
        raise  # También rechaza si un doble de pantalla no detiene la ejecución.


def resumen_carteras(vista=None):
    vista = vista_carteras() if vista is None else vista
    comunes = _resumen_carteras_comun() if vista['estado'] != 'habilitado' else None
    if comunes is not None:
        return comunes
    cabeceras = _cabeceras_carteras_comunes()
    # Resumen compartido junto con la vista de su organización; se rehace si cambian las cabeceras comunes.
    try:
        huella = int(pd.util.hash_pandas_object(cabeceras, index=False).sum())
    except Exception:
        return calculos.resumenes_efectivos(cabeceras, vista)
    with _CERROJO_VISTAS:
        registro = next((r for r in _VISTAS_ORGANIZACION.values() if r.get('vista') is vista), None)
        if registro and registro.get('resumen_huella') == huella:
            return registro['resumen']
    resumen = calculos.resumenes_efectivos(cabeceras, vista)
    with _CERROJO_VISTAS:
        if registro is not None and registro.get('vista') is vista:
            registro['resumen'], registro['resumen_huella'] = resumen, huella
    return resumen


def clases_gestion(vista=None):
    vista = vista_carteras() if vista is None else vista
    import rapidez
    return rapidez.memorizar('clases_gestion', lambda: calculos.catalogo_efectivo(_clases_gestion_comunes(), vista)).copy()


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
    columnas = ['fondo', 'instrumento', 'pct_pn', 'categoria_resumen',
                'categoria_detallada', 'moneda', 'fecha_cartera']

    def base():
        filas = calculos.filas_efectivas(vista['filas'])
        if not filas.empty:
            filas['fondo'] = filas.codigo_cnv.map(_fondos_ficha_comunes(True).set_index('codigo_cnv').nombre)
        return filas
    import rapidez
    filas = rapidez.memorizar('buscador_base', base)
    if filas.empty:
        return pd.DataFrame(columns=columnas)
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
    import acceso
    try:
        return _clasificacion_transaccion(contexto, escribir)
    finally:
        invalidar_vista_organizacion(contexto)
        acceso.invalidar_lecturas_privadas()


def guardar_clasificacion(instrumento_id, parche, version_regla, version_fuente):
    return _escribir_clasificacion(instrumento_id, "guardar", parche, version_regla, version_fuente)


def guardar_edicion_ficha(fila, valores, opciones, cancelar=False):
    """Snapshot servidor conservado al abrir; SQL valida identidad/opciones/versiones otra vez.

    Nunca recibe organización, dueño ni autor. Sin cambios/cancelación no abre conexión.
    """
    if cancelar:
        return {'estado': 'cancelado'}
    _contexto_clasificacion(escritura=True)
    parche = calculos.parche_edicion_ficha(fila, valores, opciones)
    if parche is None:
        return {'estado': 'sin_cambios'}
    return guardar_clasificacion(fila['identidad']['instrumento_id'], parche,
                                 fila['revision'] or 0, fila['version_fuente'])


def guardar_edicion_ficha_lote(filas, valores, opciones):
    """Un contexto revalidado, una transacción atómica y una invalidación por lote."""
    from uuid import UUID
    from psycopg.types.json import Jsonb
    import acceso
    contexto = _contexto_clasificacion(escritura=True)
    cambios = []
    for fila in filas:
        parche = calculos.parche_edicion_ficha(fila, valores, opciones)
        if parche is None:
            continue
        identificador = str(UUID(str(fila['identidad']['instrumento_id'])))
        vr, vf = fila['revision'] or 0, fila['version_fuente']
        if type(vr) is not int or vr < 0 or type(vf) is not int or vf < 1:
            raise calculos.ErrorClasificacion('VERSION_INVALIDA')
        calculos._aplicar_parche({}, parche)
        cambios.append((identificador, Jsonb(parche), vr, vf))
    if not cambios:
        return {'estado': 'sin_cambios', 'cantidad': 0}

    def escribir(conn):
        for parametros in cambios:
            resultado = conn.execute(
                'select public.clasificacion_guardar(%s::uuid, %s::jsonb, %s, %s)',
                parametros).fetchone()[0]
            if resultado.get('estado') != 'guardado':
                # También un rechazo devuelto como dato debe revertir las filas anteriores.
                raise calculos.ErrorClasificacion('No se guardó el lote; recargá y revisá las filas.')
        return {'estado': 'guardado', 'cantidad': len(cambios)}
    try:
        return _clasificacion_transaccion(contexto, escribir)
    finally:
        invalidar_vista_organizacion(contexto)
        acceso.invalidar_lecturas_privadas()


def resolver_fuente(instrumento_id, accion, version_regla, version_fuente):
    return _escribir_clasificacion(instrumento_id, accion, None, version_regla, version_fuente)


def resolver_novedad_ficha(fila, accion):
    """Decide sobre el snapshot abierto; SQL revalida identidad y versiones bajo bloqueo."""
    from uuid import UUID
    _contexto_clasificacion(escritura=True)
    identidad = fila.get('identidad', {})
    if (fila.get('bloqueo') or identidad.get('estado') != 'inequivoco'
            or not identidad.get('instrumento_id')
            or tuple(identidad.get('componentes') or ()) != calculos._componentes(fila)):
        raise calculos.ErrorClasificacion('IDENTIDAD_BLOQUEADA')
    if not fila.get('pendiente') or not fila.get('parche'):
        raise calculos.ErrorClasificacion('NOVEDAD_AUSENTE')
    try:
        UUID(str(identidad['instrumento_id']))
    except (ValueError, TypeError, AttributeError):
        raise calculos.ErrorClasificacion('IDENTIDAD_BLOQUEADA') from None
    return resolver_fuente(identidad['instrumento_id'], accion,
                          fila['revision'], fila['version_fuente'])


# recarga.al_dia puede releer el módulo: conservar el sello junto a los recursos sin TTL.
_VERSION_COMUN = globals().get('_VERSION_COMUN', 0)
_VERSION_CARGA = globals().get('_VERSION_CARGA', 0)
_CONSULTAS_COMUNES = globals().get('_CONSULTAS_COMUNES', {})
_CERROJO_COMUNES = globals().get('_CERROJO_COMUNES', RLock())
_CERROJO_REFRESCO = globals().get('_CERROJO_REFRESCO', Lock())
_SELLO_COMUN = globals().get('_SELLO_COMUN')
_HISTORIAS_COMPLETAS = globals().get('_HISTORIAS_COMPLETAS', {})


def version_comun():
    return _VERSION_COMUN


@st.cache_resource(max_entries=512, show_spinner=False)
def _consultar_comun(sql, params, _fallo, version=0):
    """Solo resultados SQL públicos, sin attrs privados ni pickle en cada lectura."""
    _fallo.append(True)
    return _ejecutar(sql, params)


def sello_comun():
    """Una fila de metadatos públicos; nunca lee usuarios, pedidos ni reglas privadas."""
    return tuple(_ejecutar(
        "select (select max(recibido_en) from archivos_procesados) as archivos, "
        "(select max(actualizado_en) from foto_clases) as foto, "
        "(select md5(string_agg(f::text, '|' order by codigo_cnv)) from fondos f) as catalogo, "
        "(select md5(string_agg(s::text, '|' order by tipo, codigo)) from sociedades s) as sociedades"
    ).iloc[0])


def refrescar_comunes():
    """Trabajo de fondo: sello igual no descarga; cambio publica todo junto, conservando lo anterior si falla."""
    global _VERSION_COMUN, _VERSION_CARGA, _SELLO_COMUN
    import rapidez
    if not _CERROJO_REFRESCO.acquire(blocking=False):
        return False
    try:
        sello = sello_comun()
        if sello == _SELLO_COMUN:
            return False
        if _SELLO_COMUN is None:
            _SELLO_COMUN = sello
            return False
        with _CERROJO_COMUNES:
            consultas = list(_CONSULTAS_COMUNES.values())
            # Cada intento tiene clave propia: no reciclar un resultado parcial de un refresco fallido.
            _VERSION_CARGA += 1
            version = _VERSION_CARGA
        rapidez.en_paralelo([lambda sql=sql, params=params:
            _consultar_comun(sql, params, [], version) for sql, params in consultas])
        # Si entró una consulta nueva mientras refrescábamos, se carga al primer uso de la versión nueva.
        with _CERROJO_COMUNES:
            _VERSION_COMUN, _SELLO_COMUN = version, sello
        return True
    finally:
        _CERROJO_REFRESCO.release()


def _limpiar_comunes():
    global _VERSION_COMUN, _VERSION_CARGA, _SELLO_COMUN
    with _CERROJO_COMUNES:
        _CONSULTAS_COMUNES.clear()
        _VERSION_COMUN, _VERSION_CARGA, _SELLO_COMUN = 0, 0, None
        _consultar_comun.clear()
        _serie_comun.clear()
        _indice_clases_comunes.clear()
        _historia_fondo_comun.clear()
        _rendimientos_fondo_comun.clear()
        _HISTORIAS_COMPLETAS.clear()


def consultar(sql, params=None):
    """Clave estable entre pantallas; el observador no participa en el hash.

    No normalizar SQL (puede contener literales); sí tipos escalares numpy y
    contenedores de parámetros para evitar claves distintas de un mismo valor.
    """
    import acceso
    def canonico(valor):
        if isinstance(valor, list):
            return [canonico(v) for v in valor]
        if isinstance(valor, tuple):
            return tuple(canonico(v) for v in valor)
        if type(valor).__module__.startswith('numpy'):
            return valor.item()
        return valor
    inicio = time.perf_counter()
    fallo = []
    params = canonico(params)
    with _CERROJO_COMUNES:
        _CONSULTAS_COMUNES[(sql, repr(params))] = (sql, params)
        version = _VERSION_COMUN
    # psycopg necesita listas para los parámetros ANY; mantenerlas en SQL.
    try:
        resultado = _consultar_comun(sql, params, fallo, version)
        with calculos.medir_seguimiento('COMUN_COPIA'):
            resultado = resultado.copy(deep=True)
    except TopeLectura:
        st.error('Se alcanzó el tope diario de lectura de la base. Los datos ya cargados siguen disponibles; '
                 'avisá al administrador.')
        st.stop()
        raise
    acceso.registrar_cache('COMUN_' + hashlib.sha256(sql.encode('utf-8')).hexdigest()[:8],
                          not fallo, (time.perf_counter() - inicio) * 1000)
    return resultado


consultar.clear = _limpiar_comunes


def fondos_seguidos():
    return consultar("select codigo_cnv, nombre, moneda, estado, id_ficha_web from fondos "
                     "where seguido order by nombre")


def _fondos_ficha_comunes(incluir_no_seguidos=False):
    """Cabecera y primera categoría de los seguidos; incluir_no_seguidos habilita todo el catálogo.

    patrimonio_neto = suma del patrimonio de sus clases con dato normal (sin marca de estado), en la moneda del fondo."""
    if not incluir_no_seguidos:
        # La cabecera completa ya se precalienta: no descargar otra vez los seguidos.
        todos = _fondos_ficha_comunes(True)
        return todos[todos['seguido']].copy()
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


def _cabeceras_carteras_comunes():
    """Misma lectura para el resumen legado y la vista efectiva U110."""
    return consultar(
        "select x.codigo_cnv, x.fecha_cartera, x.patrimonio from carteras_fechas x "
        "join (select codigo_cnv, max(fecha_cartera) as fecha_cartera from carteras_fechas group by codigo_cnv) u "
        "using (codigo_cnv, fecha_cartera) join fondos f using (codigo_cnv) where f.seguido")


def _resumen_carteras_comun():
    """Para el Resumen masivo: de la última cartera de cada fondo seguido, su fecha y patrimonio, la suma del % del
    PN por categoría resumen y sus 3 mayores tenencias. Devuelve (carteras, sumas, tenencias)."""
    carteras = _cabeceras_carteras_comunes()
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


def _clases_vigentes_comunes():
    """Una sola lectura común de todas las clases vigentes; el Comparador no consulta fondo por fondo."""
    return consultar("select codigo_cnv, codigo_cafci, nombre, moneda from clases "
                     "where vigente_hasta is null order by nombre")


def clases_de_fondo(codigo_cnv):
    grupos = _indice_clases_comunes(version_comun())
    clases = grupos.get(str(codigo_cnv))
    return clases.copy() if clases is not None else pd.DataFrame(columns=['codigo_cafci', 'nombre', 'moneda'])


@st.cache_resource(max_entries=4, show_spinner=False)
def _indice_clases_comunes(version):
    todas = _clases_vigentes_comunes()
    if todas.empty:
        return {}
    return {str(c): g[['codigo_cafci', 'nombre', 'moneda']].reset_index(drop=True)
            for c, g in todas.groupby('codigo_cnv')}


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


@st.cache_resource(max_entries=4, show_spinner=False)
def _serie_comun(version, _fallo=None):
    """Fuente única de series y barras R/P de todos los fondos, por versión pública."""
    if _fallo is not None:
        _fallo.append(True)
    resultado = consultar('select codigo_cafci, fecha, cuotaparte, patrimonio from valores_diarios_ultima order by fecha')
    with _CERROJO_COMUNES:
        _HISTORIAS_COMPLETAS[version] = resultado
        for anterior in sorted(_HISTORIAS_COMPLETAS)[:-4]:
            del _HISTORIAS_COMPLETAS[anterior]
    return resultado


@st.cache_resource(max_entries=4096, show_spinner=False)
def _historia_fondo_comun(version, codigo, _fallo=None):
    """Clave pública por versión/fondo; reutiliza U118 o lee solo el fondo faltante."""
    if _fallo is not None:
        _fallo.append(True)
    with _CERROJO_COMUNES:
        completa = _HISTORIAS_COMPLETAS.get(version)
    if completa is not None:
        clases = _indice_clases_comunes(version).get(str(codigo))
        codigos = [] if clases is None else clases.codigo_cafci
        return completa[completa.codigo_cafci.isin(codigos)].copy()
    return consultar('select v.codigo_cafci, v.fecha, v.cuotaparte, v.patrimonio '
                     'from valores_diarios_ultima v join clases c using (codigo_cafci) '
                     'where c.codigo_cnv = %s order by v.fecha', (codigo,))


def _fondos_de_clases(codigos, version):
    elegidos = set(codigos)
    return [int(codigo) for codigo, clases in _indice_clases_comunes(version).items()
            if elegidos.intersection(clases.codigo_cafci)]


def serie(codigos_cafci):
    """La selección solo filtra pandas; nunca participa en la clave SQL común."""
    import acceso
    inicio, fallo = time.perf_counter(), []
    version = version_comun()
    codigos = list(codigos_cafci)
    partes = [_historia_fondo_comun(version, codigo, fallo)
              for codigo in _fondos_de_clases(codigos, version)]
    if partes:
        todas = pd.concat(partes, ignore_index=True)
    else:
        with _CERROJO_COMUNES:
            completa = _HISTORIAS_COMPLETAS.get(version)
        todas = completa.iloc[:0].copy() if completa is not None else pd.DataFrame(
            columns=['codigo_cafci', 'fecha', 'cuotaparte', 'patrimonio'])
    acceso.registrar_cache('SERIE_COMUN', not fallo, (time.perf_counter() - inicio) * 1000)
    with calculos.medir_seguimiento('SERIE_FILTRAR'):
        return todas[todas.codigo_cafci.isin(codigos)].sort_values('fecha', kind='stable').reset_index(drop=True)


def rendimientos_ultimos(codigos_cafci):
    """La selección filtra resultados de fondo; nunca es un parámetro ANY de la caché."""
    import acceso
    inicio, fallo = time.perf_counter(), []
    codigos = list(codigos_cafci)
    version = version_comun()
    partes = [_rendimientos_fondo_comun(version, codigo, fallo)
              for codigo in _fondos_de_clases(codigos, version)]
    acceso.registrar_cache('FICHA_RENDIMIENTOS', not fallo, (time.perf_counter() - inicio) * 1000)
    if not partes:
        return pd.DataFrame(columns=['codigo_cafci', 'fecha', 'diario', 'dias_7', 'dias_30',
            'mes', 'anio', 'meses_12', 'revisar', 'nota', 'cuotaparte', 'patrimonio'])
    todas = pd.concat(partes, ignore_index=True)
    return todas[todas.codigo_cafci.isin(codigos)].copy()


@st.cache_resource(max_entries=4096, show_spinner=False)
def _rendimientos_fondo_comun(version, codigo, _fallo=None):
    if _fallo is not None:
        _fallo.append(True)
    return consultar(
        "select distinct on (r.codigo_cafci) r.codigo_cafci, r.fecha, r.diario, r.dias_7, r.dias_30, "
        "r.mes, r.anio, r.meses_12, r.revisar, r.nota, v.cuotaparte, v.patrimonio "
        "from rendimientos r left join valores_diarios_ultima v using (codigo_cafci, fecha) "
        "join clases c on c.codigo_cafci = r.codigo_cafci "
        "where c.codigo_cnv = %s order by r.codigo_cafci, r.fecha desc", (codigo,))


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
    import rapidez
    def cabecera():
        return consultar("select (select count(*) from fondos) as fondos, "
                  "(select count(*) from fondos where seguido) as seguidos, "
                  "(select count(*) from clases where vigente_hasta is null) as clases, "
                  "(select count(*) from valores_diarios_ultima) as valores, "
                  "(select max(fecha) from valores_diarios_ultima) as hasta, "
                  "(select count(*) from carteras_fechas) as carteras, "
                  "(select max(fecha_cartera) from carteras_fechas) as ultima_cartera, "
                  "(select count(*) from carteras where sin_clasificar) as sin_clasificar, "
                  "(select count(*) from hechos_relevantes) as hechos").iloc[0].copy()
    r, historia = rapidez.en_paralelo((cabecera, historia_por_clase))
    # Primera fecha con cuotaparte entre las clases activas (las suspendidas traen fechas de años atrás).
    r["desde"] = calculos.historia_desde(historia)
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


def catalogo_fondos():
    """Lectura privada fuera de la caché compartida del catálogo actual."""
    return _seguimiento_para_consulta(_catalogo_fondos_comun())


def _catalogo_fondos_comun():
    """Todos los fondos del catálogo (seguidos o no) para el buscador de "Sumar o quitar fondos"."""
    return consultar(
        "select f.codigo_cnv, f.nombre, f.moneda, f.seguido, f.id_ficha_web, f.tipo_fondo, f.rubro, "
        "f.descripcion_breve, f.tematica, f.tematica_provisoria, g.nombre as gerente "
        "from fondos f left join sociedades g on g.tipo = 'gerente' and g.codigo = f.codigo_gerente "
        "order by f.nombre")


_catalogo_fondos_comun.clear = consultar.clear


def pedidos_recientes(limite=60):
    """Los últimos pedidos con su estado. Sin caché: tiene que verse enseguida lo que se acaba de pedir."""
    import acceso
    if acceso.lector_seguimiento() is not None:
        try:
            return calculos.tabla_pedidos_seguimiento(acceso.listar_seguimiento(),
                _catalogo_fondos_comun().to_dict('records'), limite)
        except calculos.ErrorSeguimiento:
            st.error('No se pudieron leer los pedidos de tu organización.')
            st.stop()
            raise
    return _ejecutar(
        "select p.id, p.codigo_cnv, f.nombre as fondo, p.accion, p.descripcion_breve, p.idea_tematica, "
        "p.id_ficha_cnv, p.estado, p.motivo, p.pedido_en, p.procesado_en from pedidos_fondos p "
        "left join fondos f using (codigo_cnv) order by p.id desc limit %s", (limite,))


def anotar_pedido(codigo_cnv, accion, descripcion=None, idea=None, id_ficha=None, *, formulario=None):
    """Anota un pedido en `pedidos_fondos` (lo único que puede escribir la app, con el usuario de permiso mínimo).
    Devuelve el número del pedido."""
    import acceso
    if formulario is not None or acceso.lector_seguimiento() is not None:
        try:
            if formulario is None:
                raise calculos.ErrorSeguimiento('FORMULARIO_AUSENTE')
            solicitud = dict(formulario, codigo_cnv=int(codigo_cnv), accion=accion)
            if accion != 'dejar':
                solicitud.update({k: v for k, v in [('descripcion', descripcion), ('idea', idea)]
                                  if v is not None})
            if id_ficha is not None:
                solicitud['id_ficha'] = id_ficha
            pedido = acceso.escribir_seguimiento(_catalogo_fondos_comun().to_dict('records'), solicitud)
            return pedido['clave']
        except calculos.ErrorSeguimiento as e:
            raise ErrorPedido('Pedido no confirmado: ' + str(e)) from None
    url = _url_pedidos()
    if not url:
        raise ErrorPedido("Falta PEDIDOS_DB_URL o APP_DB_URL (archivo .env o secretos de la app): no se puede anotar el pedido.")
    try:
        if acceso._estado_rerun() is not None:
            acceso.revalidar_escritura()
        with conexion_compartida(url, connect_timeout=20) as conn:
            fila = conn.execute(
                "insert into pedidos_fondos (codigo_cnv, accion, descripcion_breve, idea_tematica, id_ficha_cnv) "
                "values (%s, %s, %s, %s, %s) returning id",
                (int(codigo_cnv), accion, descripcion, idea, id_ficha)).fetchone()
    except psycopg.Error as e:
        raise ErrorPedido("No se pudo anotar el pedido: " + " ".join(str(e).replace(url, "***").split())[:160])
    acceso.invalidar_lecturas_privadas()
    return fila[0]
