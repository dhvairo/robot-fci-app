"""Preferencias personales, disponibles solo con el interruptor del servidor."""
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
from robot.preferencias_usuario import PAGINAS, ErrorPreferencia  # noqa: E402

st.set_page_config(page_title="Preferencias", page_icon="⚙️", layout="wide")


def mostrar():
    try:
        if not acceso.preferencias_activas():
            st.info('Esta opción no está disponible.')
            return
        acceso.requerir()
        valores = calculos.preferencias_usuario(st.session_state)
        st.title('Preferencias')
        with st.form('preferencias_usuario'):
            favoritos = st.text_input('Favoritos (códigos CNV separados por coma)',
                                      value=', '.join(str(v) for v in valores['favoritos']))
            pantalla = st.selectbox('Pantalla inicial', PAGINAS,
                                    index=PAGINAS.index(valores['pantalla_inicial']))
            avisos = st.checkbox('Avisos por correo', value=valores['avisos_correo'])
            filtros = ('seguidos', 'no_seguidos', 'todos')
            filtro = st.selectbox('Filtro de seguimiento', filtros,
                                  index=filtros.index(valores['filtro_seguimiento']))
            monedas = ('pesos', 'dolares', 'todos')
            moneda = st.selectbox('Moneda del informe', monedas,
                                  index=monedas.index(valores['moneda_informe']))
            filas = st.number_input('Filas por página', min_value=10, max_value=200,
                                    value=valores['filas_por_pagina'], step=1)
            guardar = st.form_submit_button('Guardar')
            restablecer = st.form_submit_button('Restablecer')
        st.caption('Pantalla inicial y Filtro de seguimiento tienen efecto; favoritos, avisos por correo, moneda del informe y filas por página solo se guardan.')
        if guardar or restablecer:
            try:
                codigos = [int(v) for v in favoritos.replace(';', ',').split(',') if v.strip()]
            except ValueError:
                st.error('Favoritos: usar solo códigos numéricos.')
                return
            cambios = dict(favoritos=codigos,
                           pantalla_inicial=pantalla, avisos_correo=avisos,
                           filtro_seguimiento=filtro, moneda_informe=moneda,
                           filas_por_pagina=filas)
            calculos.preferencias_usuario(st.session_state, cambios=cambios,
                                         restablecer=restablecer)
            st.rerun()
    except (acceso.ErrorAcceso, ErrorPreferencia) as error:
        st.error(str(error))


mostrar()
