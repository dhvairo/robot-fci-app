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

PREFERENCIAS = ("pages/8_Preferencias.py", "Preferencias", ":material/settings:")
SUPERADMIN = ('pages/9_Superadministrador.py', 'Administración de organizaciones', ':material/admin_panel_settings:')


def secciones(preferencias=False):
    menu = dict(SECCIONES)
    if preferencias:
        menu['Administrar'] = (*menu['Administrar'], PREFERENCIAS)
    return menu


def mostrar(inicio):
    """Inicio es una función del punto de entrada; cada pantalla exige acceso."""
    import streamlit as st
    import acceso
    import calculos

    try:
        if st.session_state.get('auth'):
            import rapidez
            servidor = rapidez._SERVIDOR_PUBLICO is not None
            if servidor:
                rapidez.esperar_conexiones()
            persona = acceso._validar_sesion()
            if not persona['cambio_obligatorio']:
                if servidor and persona['rol'] in ('admin', 'asesor', 'cliente'):
                    rapidez.precalentar_sesion(persona)
                acceso.preparar_ingreso(persona)
        activa = acceso.preferencias_activas()
        inicial = 'Inicio'
        if activa and acceso.usuario():
            valores = calculos.preferencias_usuario(st.session_state)
            inicial = valores['pantalla_inicial']
    except (acceso.ErrorAcceso, ValueError) as error:
        st.error(str(error))
        st.stop()

    menu = secciones(activa)
    if acceso.roles_activos() and acceso.usuario():
        papel = acceso.rol()
        if papel == 'superadmin':
            import marca
            menu = {'Administrar': ((SUPERADMIN[0], marca.textos_panel()['titulo'], SUPERADMIN[2]),)}
            inicial = marca.textos_panel()['titulo']
        elif papel == 'cliente':
            menu.pop('Administrar', None)
        if papel != 'superadmin' and acceso.usuario().get('superadmin'):
            import marca
            menu['Plataforma'] = ((SUPERADMIN[0], marca.textos_panel()['titulo'], SUPERADMIN[2]),)
    paginas = {
        seccion: [st.Page(inicio if ruta == "Inicio.py" else ruta,
                          title=titulo, icon=icono, default=titulo == inicial)
                  for ruta, titulo, icono in pantallas]
        for seccion, pantallas in menu.items()
    }
    pagina = st.navigation(paginas, position="sidebar", expanded=True)
    estado = acceso._estado_rerun()
    if estado is not None:      # solo el nombre de la pantalla, para el registro de tiempos por pantalla
        estado['pagina'] = str(getattr(pagina, 'url_path', '') or 'Inicio')
    pagina.run()
