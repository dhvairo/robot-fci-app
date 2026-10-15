"""Filtros encadenados comunes; lógica pura y controles reutilizables."""
import calculos

AVISO_NO_SEGUIDO = ('Este fondo no se sigue: no tiene historia diaria ni cartera. '
                    'Para traerlas, pedí "Seguir" en Sumar o quitar fondos.')
CAMPOS = ("seguido", "moneda", "rubro", "categoria")
TODOS = ("Todos", "Todas", "Todos", "Todas")


def casillas(etiqueta, valores, clave, todas="Todos", formato=str, preferidas=()):
    """Casillas independientes; el control general marca/desmarca las disponibles."""
    import hashlib
    import streamlit as st
    valores = list(dict.fromkeys(valores))
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


def mostrar(fondos, clave="ficha", todas_monedas=True, moneda_todos="Todas"):
    import streamlit as st
    elegidas = []
    for nivel, (col, etiqueta) in enumerate(zip(st.columns(4), ("Fondos", "Moneda", "Rubro", "Categoría"))):
        valores = opciones(fondos, nivel, elegidas, todas_monedas)
        if not valores:
            return fondos.iloc[:0]
        if nivel == 1:
            valores = [moneda_todos if v == "Todas" else v for v in valores]
        llave = f"{clave}_filtro_{nivel}"
        if st.session_state.get(llave) not in valores:
            st.session_state[llave] = valores[0]
        valor = col.selectbox(etiqueta, valores, key=llave)
        elegidas.append("Todas" if nivel == 1 and valor == moneda_todos else valor)
    return aplicar(fondos, *elegidas)


def restablecer_informe(sesion):
    """Limpia solo controles propios; conserva el pase temporal del Comparador."""
    for llave in list(sesion):
        if llave.startswith("informe_") and llave != calculos.SELECCION_INFORME:
            del sesion[llave]
    sesion["informe_modo"] = "Búsqueda libre"
    sesion["informe_transferencia"] = list(sesion.get(calculos.SELECCION_INFORME, []))


def situacion(fondos, clave, defecto="Todos"):
    import streamlit as st
    opciones = ["Seguidos", "No seguidos", "Todos"]
    valor = st.selectbox("Fondos", opciones, index=opciones.index(defecto), key=clave)
    return aplicar(fondos, situacion=valor)
