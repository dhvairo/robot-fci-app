"""Hoja única de presentación; la entrada la inyecta una vez por ejecución."""
import re

import marca


REGLAS = """
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
    border-radius: 24px; box-shadow: 0 2px 6px rgba(80, 60, 40, 0.08);
}
[data-testid="stMetric"] { padding: 14px 18px; height: 100%; }
[data-testid="stMetricValue"] { font-size: 1.3rem; font-weight: 800; }
[data-testid="stForm"] { padding: 18px; }
[data-testid="stExpander"] summary { min-height: 44px; padding: 10px 18px; }
[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--fci-borde_suave); border-radius: 14px;
    background: var(--fci-superficie); box-shadow: 0 2px 6px rgba(80, 60, 40, 0.08);
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
@media (max-width: 640px) {
    .stMainBlockContainer { padding: 1rem; }
    .stApp h1 { font-size: 1.4rem; }
}
"""


def generar_css(config=None):
    valores = marca.estilo_visual(config)
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
