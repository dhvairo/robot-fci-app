"""Panel mínimo de gestión, solo lectura y sin datos privados de fondos."""
import streamlit as st
import pandas as pd
import recarga
recarga.al_dia()

import acceso
import marca
import formato


def mostrar():
    textos = marca.textos_panel()
    try:
        datos = acceso.panel_superadmin()
        if datos is None:
            st.info(textos['aviso'])
            return
        acceso.requerir('gestionar_organizaciones')
        textos = marca.textos_panel()
        st.title(textos['titulo'])
        st.subheader(textos['organizaciones'])
        st.dataframe(formato.tabla(pd.DataFrame(datos['organizaciones'])), hide_index=True)
        st.subheader(textos['usuarios'])
        st.dataframe(formato.tabla(pd.DataFrame(datos['usuarios'])), hide_index=True)
        st.caption(textos['validacion'])
    except acceso.ErrorAcceso:
        st.info(textos['aviso'])


mostrar()
