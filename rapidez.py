"""Velocidad (U101): derivados memorizados por sesión y PDF a pedido.

Lo caro (series, resúmenes, tablas, PDF) se arma una vez por organización, usuario y selección y se descarta al
escribir (acceso.invalidar_lecturas_privadas) o a los 5 minutos. Las pantallas solo llaman a estas funciones.
"""
import streamlit as st


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
