"""Velocidad (U101): derivados memorizados por sesión y PDF a pedido.

Lo caro (series, resúmenes, tablas, PDF) se arma una vez por organización, usuario y selección y se descarta al
escribir (acceso.invalidar_lecturas_privadas) o a los 5 minutos. Las pantallas solo llaman a estas funciones.
"""
import streamlit as st


@st.cache_resource(ttl=600, max_entries=2, show_spinner=False)
def _tarea_comun(clave, _cargar):
    """Solo dos cargas públicas; nunca composiciones privadas ni derivados de sesión."""
    return _lanzar(clave, _cargar)


def _lanzar(clave, cargar):
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
    ctx = get_script_run_ctx(suppress_warning=True)
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
    identidad = tuple(sorted(dict(persona).items()))
    anterior = st.session_state.get('_arranque_privado')
    if anterior and anterior[:2] == (identidad, contexto) and time.monotonic() - anterior[2] < 300:
        return anterior[3]
    comun = precalentar_comunes()
    def cargar():
        comun.result()
        vista = datos._cargar_vista_carteras(contexto)
        datos.resumen_carteras(vista)
        return vista
    tarea = _lanzar('CLASIFICACION', cargar)
    st.session_state['_arranque_privado'] = (identidad, contexto, time.monotonic(), tarea)
    return tarea


def vista_inicial():
    import acceso
    import datos
    anterior = st.session_state.get('_arranque_privado')
    if anterior is None:
        return None  # contratos puros y lectores sin ingreso conservan su camino
    persona = acceso.usuario()
    contexto = datos._contexto_clasificacion(persona)
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
        return (operacion.obtener(st.secrets), datos.resumen_base(),
                datos.ultimas_entradas(), datos.estado_robot())
    if not _lista('INICIO', cargar):
        return None
    return _tarea_comun('INICIO', cargar).result()


def precalentar_comunes():
    """Las mismas claves SQL que consumen Comparador e Informe; sin historias/PDF."""
    import datos
    def cargar():
        # Imports que el Comparador hace en su primera apertura; solo CPU, sin E/S.
        import plotly.express  # noqa: F401
        import consulta_puntual  # noqa: F401
        import informe  # noqa: F401
        catalogo = datos._fondos_ficha_comunes(True)
        if not catalogo.empty:
            datos._fuente_carteras_comun()
            datos._clases_gestion_comunes()
        datos.filas_a_revisar(120)
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
    persona = contexto if contexto is not None else (acceso.usuario() or {})
    identidad = tuple(sorted((k, str(v)) for k, v in dict(persona).items()))
    return acceso.memoria_derivada((clave, identidad, seleccion), calcular)


def pdf_a_pedido(clave, firma, armar, archivo, etiqueta, tipo='secondary', memorizado=True):
    """El PDF se arma solo al tocar «Preparar PDF» y se memoriza por selección; los demás clics no lo arman."""
    marca_pdf = f'pdf_{clave}'
    if st.button('Preparar PDF', key=marca_pdf + '_preparar'):
        st.session_state[marca_pdf] = firma
    if st.session_state.get(marca_pdf) == firma:
        st.download_button(etiqueta, data=memorizar(marca_pdf, armar, firma) if memorizado else armar(), file_name=archivo,
                           mime='application/pdf', on_click='ignore', type=tipo)
