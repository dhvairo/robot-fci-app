"""Contraseña de acceso a la app.

Si existe el secreto APP_PASSWORD (en los Secrets de Streamlit o en el archivo .env), la app no
muestra nada hasta que se escribe esa clave. Sin el secreto (por ejemplo, en tu PC) no pide nada.
Se llama con acceso.requerir() al principio de cada pantalla.
"""
import hmac
import os
import time

import streamlit as st


def _clave():
    try:
        v = st.secrets["APP_PASSWORD"]
    except Exception:
        v = None
    return v or os.environ.get("APP_PASSWORD")


def requerir():
    """Frena la pantalla (st.stop) si hace falta la clave y todavía no se escribió bien."""
    clave = _clave()
    if not clave or st.session_state.get("acceso_ok"):
        return
    st.title("Robot FCI")
    st.caption("Acceso restringido.")
    with st.form("acceso"):
        intento = st.text_input("Clave de acceso", type="password")
        enviar = st.form_submit_button("Entrar")
    if enviar:
        if hmac.compare_digest(intento.encode(), str(clave).encode()):
            st.session_state["acceso_ok"] = True
            st.rerun()
        time.sleep(2)  # frena los intentos de adivinar la clave
        st.error("Clave incorrecta.")
    st.stop()
