"""Única configuración de marca: organización ingresada o respaldo local histórico."""
import re
from urllib.request import urlopen

import streamlit as st

DESCARGO = ("Información de carácter exclusivamente informativo, elaborada a partir de datos públicos de la CNV. No "
            "constituye recomendación, asesoramiento ni oferta de inversión. Las carteras de los fondos cambian en forma "
            "permanente y los rendimientos pasados no garantizan rendimientos futuros. Antes de invertir, consulte el "
            "reglamento de gestión de cada fondo.")
PROPIA = {"id": "propia", "nombre": "Robot FCI", "logo": "", "color_principal": "#1F3864",
          "color_secundario": "#2E75B6", "texto_legal": DESCARGO}
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
    # Colores validados antes de incorporarlos al CSS.
    principal, secundario = config["color_principal"], config["color_secundario"]
    if not all(re.fullmatch(r"#[0-9A-Fa-f]{6}", c) for c in (principal, secundario)):
        raise ValueError("Colores de organización inválidos.")
    st.set_page_config(page_title=config["nombre"])
    st.sidebar.caption(config["nombre"])
    if config["logo"]:
        st.sidebar.image(config["logo"], width=120)
    st.markdown(f"<style>h1,h2,h3 {{color:{principal};}} "
                f"button[kind=primary] {{background-color:{secundario};}}</style>", unsafe_allow_html=True)
    st.sidebar.caption(config["texto_legal"])
