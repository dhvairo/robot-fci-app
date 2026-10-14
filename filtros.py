"""Filtros encadenados comunes; lógica pura y controles reutilizables."""
import calculos

AVISO_NO_SEGUIDO = ('Este fondo no se sigue: no tiene historia diaria ni cartera. '
                    'Para traerlas, pedí "Seguir" en Sumar o quitar fondos.')
CAMPOS = ("seguido", "moneda", "rubro", "categoria")
TODOS = ("Todos", "Todas", "Todos", "Todas")


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


def mostrar(fondos, clave="ficha", todas_monedas=True):
    import streamlit as st
    elegidas = []
    for nivel, (col, etiqueta) in enumerate(zip(st.columns(4), ("Fondos", "Moneda", "Rubro", "Categoría"))):
        valores = opciones(fondos, nivel, elegidas, todas_monedas)
        if not valores:
            return fondos.iloc[:0]
        llave = f"{clave}_filtro_{nivel}"
        if st.session_state.get(llave) not in valores:
            st.session_state[llave] = valores[0]
        elegidas.append(col.selectbox(etiqueta, valores, key=llave))
    return aplicar(fondos, *elegidas)
