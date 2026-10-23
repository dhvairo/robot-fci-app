"""Única configuración de marca: organización ingresada o respaldo local histórico."""
from urllib.request import urlopen

import streamlit as st

DESCARGO = ("Información de carácter exclusivamente informativo, elaborada a partir de datos públicos de la CNV. No "
            "constituye recomendación, asesoramiento ni oferta de inversión. Las carteras de los fondos cambian en forma "
            "permanente y los rendimientos pasados no garantizan rendimientos futuros. Antes de invertir, consulte el "
            "reglamento de gestión de cada fondo.")
PROPIA = {"id": "propia", "nombre": "Robot FCI", "logo": "", "color_principal": "#2457c5",
          "color_secundario": "#173c8c", "texto_legal": DESCARGO}
PALETA_BASE = ("#5B9BD5", "#9DC3E6", "#C9A227", "#808080", "#6AA84F", "#E06666", "#8E7CC3",
               "#F6B26B", "#76A5AF", "#B4A7D6")


def actual():
    return dict(st.session_state.get("marca") or PROPIA)


def nombre():
    return actual()["nombre"]


def paleta():
    config = actual()
    return (config["color_principal"], config["color_secundario"], *PALETA_BASE)


def logo_bytes():
    """Logo público PNG/JPEG por HTTPS; límite y timeout también para el PDF."""
    url = actual()["logo"]
    if not url:
        return None
    if not url.startswith("https://"):
        raise ValueError("El logo debe usar HTTPS.")
    with urlopen(url, timeout=10) as respuesta:
        contenido = respuesta.read(1_000_001)
    if len(contenido) > 1_000_000:
        raise ValueError("El logo supera 1 MB.")
    return contenido


def mostrar():
    config = actual()
    # Solo una actualización visual al recibir una organización distinta en la entrada.
    from estilo import generar_css
    anterior = st.session_state.get("_estilo_config_entrada")
    if anterior is not None and anterior != generar_css(config):
        st.rerun()
    st.set_page_config(page_title=config["nombre"])
    st.sidebar.caption(config["nombre"])
    if config["logo"]:
        st.sidebar.image(config["logo"], width=120)
    st.sidebar.caption(config["texto_legal"])


# Configuración visual local por organización; no requiere cambios en la base.
ESTILO_BASE = {
    "fondo": "#fbf6ef", "superficie": "#ffffff", "texto": "#2b2622",
    "tenue": "#6b5f52", "borde": "#e2d6c6", "borde_suave": "#efe8de",
    "fondo_suave": "#f6efe5", "principal": "#2457c5", "hover": "#173c8c",
    "acento_suave": "#e4ecfb", "sobre_acento": "#ffffff",
    "ok": "#2f8a57", "ok_fondo": "#dcefe3", "ok_texto": "#1f6640",
    "revisar": "#b5600f", "revisar_fondo": "#fbe6cf", "revisar_texto": "#8a4a0e",
    "peligro": "#c2362b", "peligro_fondo": "#f7d9d5", "peligro_texto": "#9b2a20",
    "fuente": "Nunito, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
}
ESTILOS_ORGANIZACION = {"propia": dict(ESTILO_BASE)}
TEXTOS_PANEL_BASE = {'titulo': 'Administración de organizaciones',
                     'aviso': 'Esta opción no está disponible.',
                     'organizaciones': 'Organizaciones', 'usuarios': 'Usuarios y roles',
                     'validacion': 'Configuración de roles validada.'}


def textos_panel():
    return {**TEXTOS_PANEL_BASE, **(actual().get('textos_superadmin') or {})}


COLORES_RUBROS = (("🔴", "#E04B4B"), ("🟠", "#F28E2B"), ("🟡", "#EDC948"),
                  ("🟢", "#59A14F"), ("🔵", "#4E79A7"), ("🟣", "#B07AA1"),
                  ("🟤", "#9C755F"), ("⚫", "#444444"))
RUBRO_GRIS = ("⚪", "#BAB0AC")


def estilo_visual(config=None):
    """Propia usa el tema local; otras organizaciones heredan sus acentos.

    `estilo` permite completar tokens sin escribir CSS en pantallas.
    """
    config = actual() if config is None else config
    valores = dict(ESTILO_BASE)
    for origen, destino in (("color_principal", "principal"), ("color_secundario", "hover")):
        if config.get(origen):
            valores[destino] = config[origen]
    valores.update(ESTILOS_ORGANIZACION.get(config.get("id"), {}))
    valores.update(config.get("estilo") or {})
    return valores
