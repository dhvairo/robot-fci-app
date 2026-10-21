"""Velocidad (U101): derivados memorizados por sesión y PDF a pedido.

Lo caro (series, resúmenes, tablas, PDF) se arma una vez por organización, usuario y selección y se descarta al
escribir (acceso.invalidar_lecturas_privadas) o a los 5 minutos. Las pantallas solo llaman a estas funciones.
"""
import streamlit as st


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
