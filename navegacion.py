"""Menú común por tarea; rutas conservadas para los saltos y la sesión."""

SECCIONES = {
    "Resumen": (
        ("Inicio.py", "Inicio", ":material/home:"),
    ),
    "Analizar": (
        ("pages/1_Ficha_por_fondo.py", "Ficha por fondo", ":material/description:"),
        ("pages/2_Comparador.py", "Comparador", ":material/compare_arrows:"),
        ("pages/3_Buscador_de_instrumentos.py", "Buscador de instrumentos", ":material/search:"),
        ("pages/5_Resumen_masivo_de_fondos.py", "Informe descriptivo", ":material/summarize:"),
        ("pages/6_Gestión_y_eficiencia.py", "Gestión y eficiencia", ":material/monitoring:"),
    ),
    "Administrar": (
        ("pages/7_Sumar_o_quitar_fondos.py", "Sumar o quitar fondos", ":material/playlist_add:"),
        ("pages/4_Filas_a_revisar.py", "Filas a revisar", ":material/fact_check:"),
    ),
}


def mostrar(inicio):
    """Inicio es una función del punto de entrada; cada pantalla exige acceso."""
    import streamlit as st

    paginas = {
        seccion: [st.Page(inicio if ruta == "Inicio.py" else ruta,
                          title=titulo, icon=icono, default=ruta == "Inicio.py")
                  for ruta, titulo, icono in pantallas]
        for seccion, pantallas in SECCIONES.items()
    }
    st.navigation(paginas, position="sidebar", expanded=True).run()
