"""Filtros encadenados comunes; lógica pura y controles reutilizables."""
import calculos

AVISO_NO_SEGUIDO = ('Este fondo no se sigue: no tiene historia diaria ni cartera. '
                    'Para traerlas, pedí "Seguir" en Sumar o quitar fondos.')
CAMPOS = ("seguido", "moneda", "rubro", "categoria")
TODOS = ("Todos", "Todas", "Todos", "Todas")


def casillas(etiqueta, valores, clave, todas="Todos", formato=str, preferidas=(),
             persistente=False, visibles=None):
    """Casillas independientes; el control general marca/desmarca las disponibles."""
    import hashlib
    import streamlit as st
    valores = list(dict.fromkeys(valores))
    if persistente:
        memoria = st.session_state.setdefault(f"{clave}_memoria", {})
        visibles = valores if visibles is None else [v for v in visibles if v in valores]
        for v in valores:
            memoria.setdefault(v, v in preferidas)
        llaves = {v: f"{clave}_casilla_{v}" for v in visibles}
        general = f"{clave}_todas"

        def guardar(v):
            memoria[v] = st.session_state[llaves[v]]

        def marcar_visibles():
            for v in visibles:
                memoria[v] = st.session_state[general]

        st.markdown(f"**{etiqueta}**")
        st.session_state[general] = bool(visibles) and all(memoria[v] for v in visibles)
        st.checkbox(todas, key=general, on_change=marcar_visibles, disabled=not visibles)
        for v, llave in llaves.items():
            st.session_state[llave] = memoria[v]
            st.checkbox(formato(v), key=llave, on_change=guardar, args=(v,))
        return [v for v in valores if memoria[v]]
    contexto = hashlib.sha256(repr(valores).encode("utf-8")).hexdigest()[:12]
    llaves = {v: f"{clave}_{contexto}_{v}" for v in valores}
    for v, llave in llaves.items():
        if llave not in st.session_state:
            st.session_state[llave] = v in preferidas
    general = f"{clave}_{contexto}_todas"
    st.session_state[general] = bool(valores) and all(st.session_state[k] for k in llaves.values())

    def marcar():
        for llave in llaves.values():
            st.session_state[llave] = st.session_state[general]

    st.markdown(f"**{etiqueta}**")
    st.checkbox(todas, key=general, on_change=marcar)
    return [v for v in valores if st.checkbox(formato(v), key=llaves[v])]


def aplicar(fondos, situacion="Seguidos", moneda="Todas", rubro="Todos", categoria="Todas"):
    """Devuelve los fondos resultantes sin modificar el catálogo de entrada."""
    base = fondos
    if situacion != "Todos":
        base = base[base["seguido"].eq(situacion == "Seguidos")]
    base = calculos.filtrar_moneda(base, moneda)
    for campo, valor, todos in (("rubro", rubro, "Todos"), ("categoria", categoria, "Todas")):
        if valor != todos:
            base = base[base[campo].eq(valor)]
    return base


def opciones(fondos, nivel, anteriores=(), todas_monedas=True):
    """Solo valores existentes tras los anteriores, más la opción que no filtra."""
    base = aplicar(fondos, *anteriores, **({"situacion": "Todos"} if not anteriores else {}))
    if nivel == 0:
        return [s for s, existe in (("Seguidos", base.seguido.any()),
                                    ("No seguidos", (~base.seguido).any()), ("Todos", True)) if existe]
    if nivel == 1:
        return (["Todas"] if todas_monedas else []) + [
            m for m in calculos.OPCIONES_MONEDA if not calculos.filtrar_moneda(base, m).empty]
    return [TODOS[nivel], *sorted(base[CAMPOS[nivel]].dropna().unique())]


def mostrar(fondos, clave="ficha", todas_monedas=True, moneda_todos="Todas", persistente=False, inicial=None):
    import streamlit as st
    elegidas = []
    for nivel, (col, etiqueta) in enumerate(zip(st.columns(4), ("Fondos", "Moneda", "Rubro", "Categoría"))):
        valores = opciones(fondos, nivel, elegidas, todas_monedas)
        if nivel == 0 and inicial is not None:
            valores = ["Seguidos", "No seguidos", "Todos"]
        if not valores:
            return fondos.iloc[:0]
        if nivel == 1:
            valores = [moneda_todos if v == "Todas" else v for v in valores]
        llave = f"{clave}_filtro_{nivel}"
        memoria = f"{llave}_memoria"
        recordar = persistente or (nivel == 0 and inicial is not None)
        if recordar and llave not in st.session_state and memoria in st.session_state:
            st.session_state[llave] = st.session_state[memoria]
        if st.session_state.get(llave) not in valores:
            st.session_state[llave] = inicial if nivel == 0 and inicial in valores else valores[0]
        valor = col.selectbox(etiqueta, valores, key=llave)
        if recordar:
            st.session_state[memoria] = valor
        elegidas.append("Todas" if nivel == 1 and valor == moneda_todos else valor)
    return aplicar(fondos, *elegidas)


def restablecer_informe(sesion):
    """Limpia solo controles propios; conserva el pase temporal del Comparador."""
    for llave in list(sesion):
        if llave.startswith("informe_") and llave != calculos.SELECCION_INFORME:
            del sesion[llave]
    sesion["informe_modo"] = "Búsqueda libre"
    sesion["informe_transferencia"] = list(sesion.get(calculos.SELECCION_INFORME, []))


def situacion(fondos, clave, defecto="Todos", inicial=None):
    import streamlit as st
    opciones = ["Seguidos", "No seguidos", "Todos"]
    memoria = f"{clave}_memoria"
    if inicial is not None:
        elegido = st.session_state.get(clave, st.session_state.get(memoria))
        st.session_state[clave] = elegido if elegido in opciones else inicial
    valor = st.selectbox("Fondos", opciones, index=opciones.index(defecto), key=clave)
    if inicial is not None:
        st.session_state[memoria] = valor
    return aplicar(fondos, situacion=valor)


def buscar_denominacion(fondos, texto):
    """Búsqueda literal parcial, sin distinguir mayúsculas; conserva orden y códigos."""
    return fondos[fondos.nombre.str.casefold().str.contains(texto.strip().casefold(), regex=False, na=False)]


def restablecer_comparador(sesion):
    """Comienza otra comparación; no toca el pase al Informe ni preferencias."""
    for llave in list(sesion):
        if llave.startswith("comparador_"):
            del sesion[llave]


def restablecer_ficha(sesion):
    """Vuelve al inicio vacío de Ficha sin tocar otras pantallas ni sus consultas."""
    for llave in list(sesion):
        if llave.startswith("ficha_"):
            del sesion[llave]
