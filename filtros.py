"""Filtros encadenados comunes; lógica pura y controles reutilizables."""
import calculos

AVISO_NO_SEGUIDO = ('Este fondo no se sigue: no tiene historia diaria ni cartera. '
                    'Para traerlas, pedí "Seguir" en Sumar o quitar fondos.')
CAMPOS = ("seguido", "moneda", "rubro", "categoria")
TODOS = ("Todos", "Todas", "Todos", "Todas")


def casillas(etiqueta, valores, clave, todas="Todos", formato=str, preferidas=(),
             persistente=False, visibles=None, al_desmarcar=None):
    """Casillas independientes; el control general marca/desmarca las disponibles."""
    import hashlib
    import streamlit as st
    valores = list(dict.fromkeys(valores))
    if persistente:
        memoria = st.session_state.setdefault(f"{clave}_memoria", {})
        conjunto, preferidas = set(valores), set(preferidas)
        visibles = valores if visibles is None else [v for v in visibles if v in conjunto]
        for v in valores:
            memoria.setdefault(v, v in preferidas)
        llaves = {v: f"{clave}_casilla_{v}" for v in visibles}
        general = f"{clave}_todas"

        def guardar(v):
            memoria[v] = st.session_state.get(llaves[v], False)
            if al_desmarcar and not memoria[v]:
                al_desmarcar(v, memoria)

        def marcar_visibles():
            for v in visibles:
                memoria[v] = st.session_state.get(general, False)

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
            st.session_state[llave] = st.session_state.get(general, False)

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
        if isinstance(valor, (list, tuple)):
            if valor:
                base = base[base[campo].isin(valor)]
        elif valor != todos:
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


def mostrar(fondos, clave="ficha", todas_monedas=True, moneda_todos="Todas", persistente=False, inicial=None, multiple=False):
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
        if multiple and nivel > 0:
            valores = sorted(valores[1:])
            anteriores = st.session_state.get(llave, st.session_state.get(memoria, []))
            st.session_state[llave] = [v for v in anteriores if v in valores] if isinstance(anteriores, list) else []
            valor = col.multiselect(etiqueta, valores, key=llave, placeholder="Todos")
            st.session_state[memoria] = valor
            elegidas.append(valor)
            continue
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


def lista_fondos(etiqueta, nombres, clave, defecto=(), vacio_todos=True, mostrar_etiquetas=True, conservar_orden=False):
    """Búsqueda nativa y selección con ×; memoria independiente de la vida del widget."""
    import streamlit as st
    opciones = list(nombres) if conservar_orden else sorted(nombres, key=lambda c: (nombres[c].casefold(), str(c)))
    memoria = clave + '_memoria'
    anterior = st.session_state.get(clave, st.session_state.get(memoria, list(defecto)))
    st.session_state[clave] = [c for c in anterior if c in nombres] if isinstance(anterior, list) else []
    # El contenedor activa el CSS existente solo para el Comparador.
    with st.container(key=None if mostrar_etiquetas else 'comparador_busqueda'):
        elegidos = st.multiselect(etiqueta, opciones, key=clave, format_func=nombres.get,
                                 placeholder="Todos · escribí para buscar" if vacio_todos else "Escribí para buscar")
    st.session_state[memoria] = list(elegidos)
    return (elegidos or opciones) if vacio_todos else elegidos


def quitar_fondo_sin_clases(sesion, codigo, memoria, clases, preferidas, clave='comparador_fondos'):
    """Si desmarcar `codigo` deja a su fondo sin clases, saca el fondo de la lista y
    devuelve sus clases a la selección por defecto (así no vuelve vacío)."""
    fondo = clases.get(codigo)
    hermanas = [c for c, f in clases.items() if f == fondo]
    if any(memoria.get(c) for c in hermanas):
        return False
    for c in hermanas:
        memoria[c] = c in preferidas
    actuales = sesion.get(clave, sesion.get(clave + '_memoria', []))
    sesion[clave + '_memoria'] = [f for f in actuales if f != fondo]
    sesion.pop(clave, None)
    return True


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
    """Comienza otra comparación vacía y retira el pase al Informe."""
    sesion.pop(calculos.SELECCION_INFORME, None)
    sesion.pop(calculos.SELECCION_GESTION, None)
    for llave in list(sesion):
        if llave.startswith("comparador_"):
            del sesion[llave]


def restablecer_ficha(sesion):
    """Vuelve al inicio vacío de Ficha sin tocar otras pantallas ni sus consultas."""
    for llave in list(sesion):
        if llave.startswith("ficha_"):
            del sesion[llave]
