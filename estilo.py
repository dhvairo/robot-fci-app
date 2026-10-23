"""Hoja única de presentación; la entrada la inyecta una vez por ejecución."""
import re
from functools import lru_cache

import marca


REGLAS = """
.st-key-comparador_busqueda [data-baseweb="tag"] { display: none; }
.st-key-comparador_fila_clases [data-testid="stCheckbox"] { flex: 0 0 auto; }
.stApp { background: var(--fci-fondo); color: var(--fci-texto); }
[data-testid="stAppViewContainer"], [data-testid="stHeader"] { background: var(--fci-fondo); }
.stApp, .stApp input, .stApp textarea, .stApp button,
[data-testid="stMarkdownContainer"], [data-testid="stMetric"] { font-family: var(--fci-fuente); }
[data-testid="stSidebar"] { background: var(--fci-superficie); border-right: 1px solid var(--fci-borde_suave); }
[data-testid="stSidebarNav"] a { border-radius: 14px; }
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: var(--fci-acento_suave); color: var(--fci-principal); font-weight: 800;
}
.stMainBlockContainer { padding-top: 2rem; padding-bottom: 3rem; }
[data-testid="stVerticalBlock"] { gap: 1rem; }
.stApp h1, .stApp h2, .stApp h3 { color: var(--fci-texto); line-height: 1.25; font-weight: 800; }
.stApp h1 { font-size: 1.8rem; font-weight: 900; padding-bottom: 1rem; }
.stApp h2 { font-size: 1.3rem; }
.stApp h3 { font-size: 1.05rem; }
[data-testid="stCaptionContainer"], [data-testid="stMetricLabel"] { color: var(--fci-tenue); }
[data-testid="stMetric"], [data-testid="stForm"], [data-testid="stExpander"],
[data-testid="stVerticalBlockBorderWrapper"] > div {
    background: var(--fci-superficie); border: 1px solid var(--fci-borde_suave);
    border-radius: 24px; box-shadow: 0 2px 6px color-mix(in srgb, var(--fci-texto) 8%, transparent);
}
[data-testid="stMetric"] { padding: 14px 18px; height: 100%; }
[data-testid="stMetricValue"] { font-size: 1.3rem; font-weight: 800; }
[data-testid="stForm"] { padding: 18px; }
[data-testid="stExpander"] summary { min-height: 44px; padding: 10px 18px; }
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--fci-borde_suave); border-radius: 14px;
    background: var(--fci-superficie); box-shadow: 0 2px 6px color-mix(in srgb, var(--fci-texto) 8%, transparent);
}
[data-testid="stTable"] table { border-collapse: collapse; width: 100%; }
[data-testid="stTable"] th { color: var(--fci-tenue); font-weight: 700; background: var(--fci-fondo_suave); }
[data-testid="stTable"] th, [data-testid="stTable"] td { border-bottom: 1px solid var(--fci-borde_suave); padding: 10px 14px; }
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button, [data-testid="stLinkButton"] a {
    border-radius: 999px; min-height: 44px; padding: 10px 20px; font-weight: 700;
    border: 1px solid var(--fci-borde); background: var(--fci-superficie); color: var(--fci-texto);
}
[data-testid="stButton"] button:hover, [data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover, [data-testid="stLinkButton"] a:hover {
    border-color: var(--fci-principal); background: var(--fci-acento_suave); color: var(--fci-principal);
}
.stApp button[kind="primary"], .stApp a[kind="primary"] {
    background: var(--fci-principal); color: var(--fci-sobre_acento); border-color: var(--fci-principal);
}
.stApp button[kind="primary"]:hover, .stApp a[kind="primary"]:hover {
    background: var(--fci-hover); color: var(--fci-sobre_acento);
}
.stApp button:disabled { opacity: 0.6; cursor: not-allowed; }
.stApp input, .stApp textarea, [data-baseweb="select"] > div {
    border-radius: 14px; color: var(--fci-texto); background: var(--fci-superficie);
}
.stApp button:focus-visible, .stApp a:focus-visible, .stApp input:focus-visible,
.stApp textarea:focus-visible { outline: 3px solid var(--fci-principal); outline-offset: 2px; }
.stApp button, .stApp input, [data-baseweb="select"] > div { min-height: 44px; }
[data-testid="stCheckbox"] label, [data-testid="stRadio"] label,
[data-testid="stSidebarNav"] a, [data-testid="stToggle"] label { min-height: 44px; }
[data-testid="stAlertContainer"] { border-radius: 14px; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {
    background: var(--fci-ok_fondo); color: var(--fci-ok_texto); border: 1px solid var(--fci-ok);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {
    background: var(--fci-revisar_fondo); color: var(--fci-revisar_texto); border: 1px solid var(--fci-revisar);
}
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {
    background: var(--fci-peligro_fondo); color: var(--fci-peligro_texto); border: 1px solid var(--fci-peligro);
}
.fci-tarjetas { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); }
.fci-tarjeta { background: var(--fci-superficie); border: 1px solid var(--fci-borde);
    border-radius: 24px; padding: 18px; overflow-wrap: anywhere; min-width: 0;
    box-shadow: 0 2px 6px color-mix(in srgb, var(--fci-texto) 8%, transparent); }
.fci-tarjeta h3 { color: var(--fci-principal); }
.fci-tarjeta dl { margin: 0; }
.fci-tarjeta dt { color: var(--fci-tenue); font-size: .85rem; }
.fci-tarjeta dd { margin: 0 0 .6rem; white-space: pre-wrap; }
@media (max-width: 1024px) {
    .stMainBlockContainer { padding: 1.5rem; max-width: 100%; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
    [data-testid="stColumn"] { min-width: min(100%, 240px); }
    .stApp h1 { font-size: 1.6rem; }
}
@media (max-width: 640px) {
    .stMainBlockContainer { padding: 1rem; }
    .stApp h1 { font-size: 1.4rem; }
    .stApp h2 { font-size: 1.15rem; }
    [data-testid="stHorizontalBlock"] { flex-direction: column; }
    [data-testid="stColumn"] { width: 100% !important; flex: 1 1 100% !important; min-width: 0; }
    [data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button,
    [data-testid="stDownloadButton"] button, [data-testid="stLinkButton"] a,
    [data-baseweb="select"], [data-testid="stTextInput"] { width: 100%; }
    [data-testid="stCheckbox"] label { width: 100%; }
    [data-testid="stMain"], [data-testid="stVerticalBlock"] { min-width: 0; max-width: 100%; }
    [data-testid="stTable"] { overflow-x: auto; }
    [data-testid="stTabs"] [role="tablist"] { overflow-x: auto; }
    .stApp h1, .stApp h2, .stApp h3 { overflow-wrap: anywhere; }
    .fci-tarjetas { grid-template-columns: minmax(0, 1fr); }
}
"""


@lru_cache(maxsize=32)
def _css_validado(items):
    valores = dict(items)
    return _construir_css(valores)


def generar_css(config=None):
    return _css_validado(tuple(marca.estilo_visual(config).items()))


def _construir_css(valores):
    for clave in marca.ESTILO_BASE:
        valor = valores[clave]
        patron = r"[a-zA-Z0-9 ,'\-]+" if clave == "fuente" else r"#[0-9a-fA-F]{6}"
        if not isinstance(valor, str) or not re.fullmatch(patron, valor):
            raise ValueError("Estilo de organización inválido.")
    variables = "\n".join(f"--fci-{k}: {valores[k]};" for k in marca.ESTILO_BASE)
    return ":root {\n" + variables + "\n}\n" + REGLAS


def regla_boton_serie(codigo, color, visible):
    """Conserva el indicador visual encendido/apagado del Comparador."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Color de serie inválido.")
    return (f".st-key-comparador_serie_{int(codigo)} button {{"
            f"border: 2px solid {color}; color: {color};"
            f"opacity: {1 if visible else 0.5};"
            f"text-decoration: {'none' if visible else 'line-through'};"
            "}")


def inyectar(contenedor):
    """Usa la organización cargada durante el acceso, sin otra hoja por pantalla."""
    import streamlit as st
    series = st.session_state.get("comparador_series", {})
    reglas = "\n".join(regla_boton_serie(codigo, control["color"], control["visible"])
                       for codigo, control in series.items())
    contenedor.markdown("<style>" + generar_css() + reglas + "</style>", unsafe_allow_html=True)


def presentar(contenedor, pantalla):
    """Una sola hoja en la entrada, también cuando la pantalla se detiene.

    Se actualiza el mismo nodo al terminar para incluir las series recién elegidas;
    nunca se agregan hojas por pantalla o por botón.
    """
    import streamlit as st
    st.session_state["_estilo_config_entrada"] = generar_css()
    inyectar(contenedor)
    try:
        pantalla()
    finally:
        inyectar(contenedor)
