"""Velocidad (U101): derivados memorizados por sesión y PDF a pedido.

Lo caro (series, resúmenes, tablas, PDF) se arma una vez por organización, usuario y selección y se descarta al
escribir (acceso.invalidar_lecturas_privadas) o a los 5 minutos. Las pantallas solo llaman a estas funciones.
"""
import streamlit as st


@st.cache_resource(max_entries=32, show_spinner=False)
def _recurso_tarea(clave, version, _cargar):
    """Cargas públicas por versión; nunca composiciones privadas ni derivados de sesión."""
    return _lanzar(clave, _cargar, publica=True)


def _tarea_comun(clave, _cargar):
    import datos
    return _recurso_tarea(clave, datos.version_comun(), _cargar)


_tarea_comun.clear = _recurso_tarea.clear


def en_paralelo(cargas):
    """No transmite contexto de sesión a los seis trabajadores de datos públicos."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix='lectura-comun') as pool:
        tareas = [pool.submit(cargar) for cargar in cargas]
        return tuple(tarea.result(timeout=TIEMPO_ESPERA) for tarea in tareas)


def _lanzar(clave, cargar, publica=False):
    """El trabajador entrega un resultado: nunca escribe estado de sesión ni dibuja."""
    from concurrent.futures import Future
    from threading import Thread
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    import calculos

    resultado = Future()

    def ejecutar():
        try:
            with calculos.medir_seguimiento('ARRANQUE_' + clave):
                resultado.set_result(cargar())
        except BaseException:
            # No conservar mensajes/conexiones en la caché compartida.
            resultado.set_exception(RuntimeError('No se pudo completar la carga inicial.'))
        finally:
            from threading import current_thread
            hilo = current_thread()
            if hasattr(hilo, 'streamlit_script_run_ctx'):
                del hilo.streamlit_script_run_ctx

    hilo = Thread(target=ejecutar, name='carga-comun-' + clave, daemon=True)
    ctx = None if publica else get_script_run_ctx(suppress_warning=True)
    if ctx is not None:
        add_script_run_ctx(hilo, ctx)
    hilo.start()
    return resultado


def _lista(clave, cargar):
    return _esperar(_tarea_comun(clave, cargar))


TIEMPO_ESPERA = 60


def _esperar(tarea):
    """Espera el mismo trabajo en esta corrida, con un límite y reintento explícito."""
    from concurrent.futures import TimeoutError
    try:
        if tarea.done():
            tarea.result()
        else:
            with st.spinner('Cargando…'):
                tarea.result(timeout=TIEMPO_ESPERA)
    except (TimeoutError, RuntimeError) as error:
        _tarea_comun.clear()
        st.session_state.pop('_arranque_privado', None)
        st.session_state.pop('_arranque_ficha', None)
        st.session_state.pop('_arranque_seguimiento', None)
        mensaje = ('La carga inicial superó el tiempo de espera.' if isinstance(error, TimeoutError)
                   else 'No se pudo completar la carga inicial.')
        st.error(mensaje + ' Volvé a abrir la pantalla para reintentar.')
        st.stop()
    return True


def precalentar_sesion(persona):
    """Una tarea privada por sesión/identidad, invalidada junto con las lecturas."""
    import time
    import datos
    contexto = datos._contexto_clasificacion(persona)
    contexto = dict(contexto, _version_comun=datos.version_comun())
    identidad = tuple(sorted(dict(persona).items()))
    anterior = st.session_state.get('_arranque_privado')
    if anterior and anterior[:2] == (identidad, contexto) and time.monotonic() - anterior[2] < 300:
        return anterior[3]
    precalentar_comunes()
    def cargar():
        # No esperar catálogo, Gestión, Filas ni imports para tener la clasificación.
        return datos._cargar_vista_carteras(contexto)
    tarea = _lanzar('CLASIFICACION', cargar)
    st.session_state['_arranque_privado'] = (identidad, contexto, time.monotonic(), tarea)
    def ficha():
        import calculos
        comunes = datos._fondos_ficha_comunes(True)
        vista = tarea.result(timeout=TIEMPO_ESPERA)
        return calculos.catalogo_efectivo(comunes, vista), vista
    st.session_state['_arranque_ficha'] = _lanzar('FICHA', ficha, publica=True)
    import acceso
    lector = acceso.lector_seguimiento()
    if lector is not None and isinstance(lector[0], datos.RepositorioSeguimiento):
        backend, resolver = lector
        validado = dict(resolver())
        par = (validado['organizacion'], validado['dueno'])
        lectura = _lanzar('SEGUIMIENTO', lambda: backend._operar(
            par, backend._leer, _contexto=validado), publica=True)
        st.session_state['_arranque_seguimiento'] = (
            getattr(backend, 'clave_cache', id(backend)), validado, lectura)
    return tarea


def vista_inicial():
    import acceso
    import datos
    anterior = st.session_state.get('_arranque_privado')
    if anterior is None:
        return None  # contratos puros y lectores sin ingreso conservan su camino
    persona = acceso.usuario()
    contexto = dict(datos._contexto_clasificacion(persona), _version_comun=datos.version_comun())
    tarea = precalentar_sesion(persona)
    if contexto != st.session_state['_arranque_privado'][1]:
        return None
    if not _esperar(tarea):
        st.stop()
    return tarea.result()


def inicio():
    """Lectores comunes de Inicio, sin memoria ni identidad de usuario en el hilo."""
    import datos
    import operacion
    def cargar():
        return en_paralelo((lambda: operacion.obtener(st.secrets), datos.resumen_base,
                            datos.ultimas_entradas, datos.estado_robot))
    if not _lista('INICIO', cargar):
        return None
    resultado = _tarea_comun('INICIO', cargar).result()
    return (_OPERACION_PUBLICA or resultado[0], *resultado[1:])


def filas_iniciales(dias):
    import datos
    return leer_comun('FILAS_' + str(dias), lambda: datos.filas_a_revisar(dias))


def leer_comun(clave, cargar):
    tarea = _tarea_comun(clave, cargar)
    _esperar(tarea)
    return tarea.result()


def precalentar_comunes():
    """Las mismas claves SQL que consumen Comparador e Informe; incluyendo la historia común para barras R/P."""
    import datos
    def cargar():
        def catalogo_clases():
            import calculos
            return _tarea_comun('COMPARADOR_CATALOGO_CLASES', lambda: calculos.clases_comparador(
                datos._fondos_ficha_comunes(True)[['codigo_cnv', 'nombre']], [], datos.clases_de_fondo)
                ).result(timeout=TIEMPO_ESPERA)
        return en_paralelo((catalogo_clases,
                            datos._fuente_carteras_comun, datos._clases_gestion_comunes,
                            lambda: datos._indice_clases_comunes(datos.version_comun()),
                            lambda: datos.filas_a_revisar(120),
                            datos._cabeceras_carteras_comunes, datos._catalogo_fondos_comun,
                            lambda: datos._fondos_ficha_comunes(False),
                            lambda: datos._serie_comun(datos.version_comun())))
    return _tarea_comun('CATALOGO', cargar)


def comunes_listos():
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is None:
        return True  # contratos puros y consola conservan la lectura sin hilo
    tarea = precalentar_comunes()
    return _esperar(tarea)


def grafico_memorizado(clave, figura, *seleccion):
    """Serializa una vez el gráfico de lectura, como la tabla memorizada."""
    import plotly.io
    from streamlit.proto.PlotlyChart_pb2 import PlotlyChart as PlotlyChartProto
    from streamlit.elements.lib.layout_utils import LayoutConfig
    from streamlit.elements.lib.utils import compute_and_register_element_id

    def construir():
        proto = PlotlyChartProto()
        proto.theme = 'streamlit'
        proto.spec = plotly.io.to_json(figura, validate=False)
        proto.config = '{}'
        return proto
    proto = memorizar(clave + '_grafico', construir, *seleccion)
    proto.id = compute_and_register_element_id('plotly_chart', user_key=clave, key_as_main_identity=False,
        dg=st._main, plotly_spec=proto.spec, plotly_config=proto.config,
        theme='streamlit', width='stretch', height='content')
    return st._main._enqueue('plotly_chart', proto, layout_config=LayoutConfig(width='stretch'))


def tabla_memorizada(clave, tabla, *seleccion):
    """Conserva también la serialización del Styler, privada por sesión.

    Adaptador de Streamlit: solo tabla de lectura, sin estado de selección. Mantiene
    números ordenables y formato argentino; evita traducir miles de celdas por clic.
    """
    from streamlit.elements import arrow
    from streamlit.elements.lib.layout_utils import LayoutConfig
    moderno = hasattr(arrow, 'DataframeProto')

    def construir():
        proto = arrow.DataframeProto() if moderno else arrow.ArrowProto()
        destino = proto.arrow_data if hasattr(proto, 'arrow_data') else proto
        arrow.marshall(destino, tabla, default_uuid=clave)
        proto.columns = '{"_index": {"hidden": true}}'
        if hasattr(proto, 'use_container_width'):
            proto.use_container_width = True
        return proto
    proto = memorizar(clave + '_serializada', construir, *seleccion)
    return st._main._enqueue('dataframe' if moderno else 'arrow_data_frame', proto,
                             layout_config=LayoutConfig(width='stretch'))


def memorizar(clave, calcular, *seleccion, contexto=None):
    """Derivado memorizado por sesión, organización, usuario y selección liviana.

    La identidad sale de la persona ya validada por acceso.requerir() al comienzo de la pantalla, o del contexto
    validado que pase quien llama (datos.fondos_ficha)."""
    import acceso
    import datos
    persona = contexto if contexto is not None else (acceso.usuario() or {})
    identidad = tuple(sorted((k, str(v)) for k, v in dict(persona).items()))
    import time
    import calculos
    fallo = []
    def construir():
        fallo.append(True)
        with calculos.medir_seguimiento(clave.upper()):
            return calcular()
    inicio = time.perf_counter()
    resultado = acceso.memoria_derivada((clave, identidad, datos.version_comun(), seleccion), construir)
    acceso.registrar_cache(clave.upper(), not fallo, (time.perf_counter() - inicio) * 1000)
    return resultado


from threading import Event
_POOLS_INICIADOS = globals().get('_POOLS_INICIADOS', Event())


_OPERACION_PUBLICA = globals().get('_OPERACION_PUBLICA')
_SERVIDOR_PUBLICO = globals().get('_SERVIDOR_PUBLICO')


def _imports_comunes():
    import plotly.express  # noqa: F401
    import consulta_puntual  # noqa: F401
    import pandas.io.formats.style  # noqa: F401; primer Styler, sin datos ni UI.
    # Primer enqueue: inspect.getmodule recorre todos los módulos en Windows.
    # Calentar su índice y Arrow fuera de la primera pantalla, sin dibujar.
    import inspect
    import pandas as pd
    from streamlit import dataframe_util, string_util
    dataframe_util.convert_pandas_df_to_arrow_bytes(pd.DataFrame({'dato': [0]}))
    string_util.validate_icon_or_emoji('📈')  # Cargar el catálogo de emojis del menú.
    inspect.getmodule(None, '<precalentado>')  # Indexar módulos, incluso sin frames de la app.


def esperar_conexiones():
    """El ingreso recoge el arranque del pool, sin abrir conexiones en el hilo de UI."""
    if _POOLS_INICIADOS.is_set():
        return True
    _esperar(_tarea_comun('CONEXIONES', __import__('datos').preparar_conexiones))


def _ciclo_servidor():
    global _OPERACION_PUBLICA
    import datos
    import operacion
    if not _POOLS_INICIADOS.is_set():
        _tarea_comun('CONEXIONES', datos.preparar_conexiones).result(timeout=TIEMPO_ESPERA)
        _POOLS_INICIADOS.set()
    else:
        datos.preparar_conexiones()
    datos.refrescar_comunes()
    catalogo = precalentar_comunes()
    def inicio_publico():
        return en_paralelo((lambda: operacion.obtener(st.secrets), datos.resumen_base,
                            datos.ultimas_entradas, datos.estado_robot))
    inicio = _tarea_comun('INICIO', inicio_publico)
    _tarea_comun('IMPORTS', _imports_comunes)  # CPU independiente de los datos; sin historias ni PDF.
    catalogo.result(timeout=TIEMPO_ESPERA)
    inicio.result(timeout=TIEMPO_ESPERA)
    _OPERACION_PUBLICA = operacion.obtener(st.secrets)  # Mantiene el TTL propio de Actions.


@st.cache_resource(show_spinner=False)
def iniciar_servidor():
    """Primer import, antes de ingresar: solo fuentes comunes; ninguna marca ni identidad privada."""
    global _SERVIDOR_PUBLICO
    if _SERVIDOR_PUBLICO is not None and not _SERVIDOR_PUBLICO.done():
        return _SERVIDOR_PUBLICO  # También después de recargar código: un solo monitor público.
    from threading import Event
    def mantener():
        pausa = Event()
        while True:
            try:
                _ciclo_servidor()
            except Exception:
                # No descartar datos previos ni guardar detalles de la conexión.
                import logging
                logging.getLogger('seguimiento').warning('SEGUIMIENTO ARRANQUE_PUBLICO pendiente; reintento en 60 s')
                _tarea_comun.clear()  # Reintentar tareas fallidas; conservar todos los resultados SQL anteriores.
            pausa.wait(60)  # Solo comprueba sello; no vence ningún dato por reloj.
    _SERVIDOR_PUBLICO = _lanzar('SERVIDOR', mantener, publica=True)
    return _SERVIDOR_PUBLICO


def _al_importar():
    import os
    import datos
    from streamlit.runtime import exists
    # Consola/verificaciones no arrancan servicios. No depende de Auth ni session_state.
    if os.environ.get('ROBOT_FCI_SIN_RED') != '1' and exists() and datos._url():
        iniciar_servidor()


_al_importar()


def pdf_a_pedido(clave, firma, armar, archivo, etiqueta, tipo='secondary', memorizado=True):
    """El PDF se arma solo al tocar «Preparar PDF» y se memoriza por selección; los demás clics no lo arman."""
    marca_pdf = f'pdf_{clave}'
    if st.button('Preparar PDF', key=marca_pdf + '_preparar'):
        st.session_state[marca_pdf] = firma
    if st.session_state.get(marca_pdf) == firma:
        st.download_button(etiqueta, data=memorizar(marca_pdf, armar, firma) if memorizado else armar(), file_name=archivo,
                           mime='application/pdf', on_click='ignore', type=tipo)
