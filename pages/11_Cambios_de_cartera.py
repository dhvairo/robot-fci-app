"""Cambios de cartera: selección independiente y comparación de snapshots guardados."""
import streamlit as st
import recarga
recarga.al_dia()
import acceso
import calculos
import datos
import formato
import pandas as pd

ETIQUETAS = dict(zip(calculos.CAMPOS_CLASIFICACION, ('Rubro', 'Categoría', 'Moneda', 'Indexación')))

def mostrar_cambios_cartera(codigo):
    with st.spinner('Leyendo carteras guardadas…'):
        try:
            historia = datos.cambios_cartera(codigo)
        except datos.TopeLectura:
            st.warning('Se alcanzó el tope diario de lectura. Volvé a intentar mañana.')
            return
        except (datos.psycopg.Error, ValueError):
            st.warning('No se pudo reconstruir el historial de carteras guardadas.')
            return
    if not historia:
        st.info('Todavía no hay carteras guardadas para comparar.')
        return
    fechas = [c['fecha'] for c in historia]
    fecha = st.selectbox('Fecha de cartera a comparar', fechas, format_func=formato.fecha,
                         key=f'fecha_cambios_{codigo}')
    cambio = next(c for c in historia if c['fecha'] == fecha)
    st.caption('Comparación reproducida desde carteras guardadas y su clasificación de cada fecha. '
               'Los cambios son descriptivos: no acreditan operaciones ni evalúan la gestión. '
               'La rotación es la mitad de la suma de las diferencias absolutas de peso, incluido Resto.')
    if cambio['anterior'] is not None:
        st.caption(f"{formato.fecha(cambio['anterior'])} → {formato.fecha(cambio['fecha'])} · "
                   f"{cambio['dias']} días · {cambio['fuente_anterior']} → {cambio['fuente_actual']}")
    for aviso in cambio['avisos']:
        st.warning(aviso)
    if cambio['rotacion'] is None:
        st.info(cambio['estado'])
    else:
        st.metric('Rotación observada' if cambio['parcial'] else 'Rotación de cartera',
                  f"{formato.numero(cambio['rotacion'])} %")
        etiquetas = {'instrumento': 'Activo', 'antes': 'Antes %', 'despues': 'Después %',
                     'diferencia': 'Diferencia pp', 'coincidencia': 'Identificación',
                     'movimiento': 'Movimiento', 'campo': 'Clasificación', 'identidad': 'Identidad'}
        def tabla(filas):
            base = pd.DataFrame(filas).rename(columns=etiquetas)
            if 'Clasificación' in base:
                base['Clasificación'] = base['Clasificación'].map(
                    {'rubro_cnv': 'Rubro CNV', **ETIQUETAS})
            st.dataframe(formato.tabla(base, numeros={'Antes %': 2, 'Después %': 2, 'Diferencia pp': 2}),
                          hide_index=True, width='stretch')
        for campo, titulo in (('entraron', 'Entraron'), ('salieron', 'Salieron del detalle' if cambio['parcial'] else 'Salieron'),
                              ('cambio_peso', 'Cambió peso'), ('clasificacion', 'Cambio por tipo de activo / clasificación')):
            st.markdown(f'**{titulo}**')
            if cambio[campo]:
                tabla(cambio[campo])
            else:
                st.caption('Sin cambios registrados.')
        if cambio['resto'] is not None:
            st.markdown('**Variación del Resto**')
            tabla([cambio['resto']])
    st.markdown('**Historial de comparaciones**')
    st.dataframe(formato.tabla(pd.DataFrame([
        {'Cartera': c['fecha'], 'Anterior': c['anterior'], 'Días': c['dias'], 'Rotación %': c['rotacion'],
         'Estado': c['estado'], 'Comparación parcial': c['parcial'],
         'Fuente anterior': c['fuente_anterior'], 'Fuente actual': c['fuente_actual']}
        for c in historia]), fechas=['Cartera', 'Anterior'], numeros={'Rotación %': 2}),
        hide_index=True, width='stretch')


st.set_page_config(page_title="Cambios de cartera", page_icon=":material/history:", layout="wide")
acceso.requerir()
st.title("Cambios de cartera")
todos = datos.fondos_ficha(incluir_no_seguidos=True).sort_values("nombre", key=lambda s: s.str.casefold())
codigos = todos["codigo_cnv"].tolist()
# Desvincular la selección de Ficha de su widget para conservarla entre páginas.
ficha = st.session_state.get("ficha_fondo")
if ficha is not None:
    st.session_state["ficha_fondo"] = ficha
if "cambios_fondo" not in st.session_state or st.session_state.get("cambios_origen") != ficha:
    st.session_state["cambios_fondo"] = ficha if ficha in codigos else None
st.session_state["cambios_origen"] = ficha
if st.session_state.get("cambios_fondo") not in codigos:
    st.session_state["cambios_fondo"] = None
nombres = todos.set_index("codigo_cnv")["nombre"]
codigo = st.selectbox("Fondo", codigos, index=None, key="cambios_fondo",
                      placeholder="Elegí un fondo",
                      format_func=lambda c: f"{nombres.loc[c]} · CNV {c}")
if codigo is None:
    st.info("Elegí un fondo")
    st.stop()
mostrar_cambios_cartera(int(codigo))
