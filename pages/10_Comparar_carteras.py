"""Comparar carteras: activos y emisores compartidos entre 2 a 6 fondos (solapamiento conocido)."""
import streamlit as st

import recarga
recarga.al_dia()

import acceso
import calculos
import datos
import filtros
import formato

st.set_page_config(page_title="Comparar carteras", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Comparar carteras")
st.caption("Qué tienen en común y qué no las carteras de varios fondos. Pesos sobre el patrimonio de cada fondo, "
           "sin ajustar al 100 %. Se calcula solo al elegir los fondos, con la cartera ya cargada.")

todos = datos.fondos_ficha()
if todos.empty:
    st.info("No hay fondos seguidos con cartera.")
    st.stop()
vista = todos.attrs.get('clasificacion')
nombres = dict(zip(todos.codigo_cnv, todos.nombre))
elegidos = filtros.lista_fondos("Fondos a comparar (de 2 a %d)" % calculos.MAX_FONDOS_SOLAPAMIENTO, nombres,
                                "solapamiento_fondos", vacio_todos=False)
if len(elegidos) < 2:
    st.session_state.pop(calculos.SELECCION_INFORME, None)
    st.info("Elegí al menos dos fondos para comparar sus carteras.")
    st.stop()
if len(elegidos) > calculos.MAX_FONDOS_SOLAPAMIENTO:
    st.warning(f"Se comparan como máximo {calculos.MAX_FONDOS_SOLAPAMIENTO} fondos; se usan los primeros.")
    elegidos = elegidos[:calculos.MAX_FONDOS_SOLAPAMIENTO]

with datos.seccion_disponible():
    entradas = []
    for codigo in elegidos:
        encabezado = datos.cartera_encabezado(codigo, vista)
        cartera = datos.cartera(codigo, vista)
        fila = encabezado.iloc[0] if not encabezado.empty else None
        entradas.append({"codigo_cnv": codigo, "nombre": nombres[codigo], "cartera": cartera,
                         "fecha_cartera": None if fila is None else fila["fecha_cartera"],
                         "fuente": None if fila is None else fila.get("fuente", "cnv")})
    resultado = calculos.solapamiento_carteras(entradas)
    for aviso in resultado["avisos"]:
        st.warning(aviso)
    if not resultado["fondos"]:
        st.stop()

    llevar = st.toggle("Llevar estos fondos al Informe descriptivo", key="solapamiento_llevar")
    if llevar:
        calculos.enviar_al_informe(st.session_state, elegidos)
    else:
        st.session_state.pop(calculos.SELECCION_INFORME, None)

    fondos = resultado["fondos"]
    resumen = resultado["resumen"]
    st.subheader("Fechas, parte conocida y Resto")
    st.dataframe(formato.tabla(resumen, fechas=["Cartera al"], numeros={"Conocido %": 2, "Resto %": 2}),
                 hide_index=True, width="stretch")

    def matriz(m):
        return formato.tabla(m.reset_index(names="Fondo"), numeros={f: 2 for f in fondos})

    st.subheader("Solapamiento conocido por pares (% del patrimonio)")
    st.caption("Suma del menor peso de cada activo (o emisor) entre los dos fondos. La diagonal es la parte "
               "conocida del fondo. El Resto no se cuenta.")
    c1, c2 = st.columns(2)
    c1.markdown("**Por activo**")
    c1.dataframe(matriz(resultado["matriz_activos"]), hide_index=True, width="stretch")
    c2.markdown("**Por emisor** (un emisor puede tener varias especies)")
    c2.dataframe(matriz(resultado["matriz_emisores"]), hide_index=True, width="stretch")
    st.caption("El emisor se deduce del nombre de la especie; no existe un grupo económico en la clasificación.")

    def compartidos(tabla):
        return formato.tabla(tabla, numeros={c: 2 for c in tabla.columns if c in fondos or c == "En común (mín.)"}
                             ).fillna(formato.VACIO)

    st.subheader("Activos compartidos")
    if resultado["compartidos"].empty:
        st.info("No hay activos compartidos entre estos fondos.")
    else:
        st.dataframe(compartidos(resultado["compartidos"]), hide_index=True, width="stretch")
    st.subheader("Emisores compartidos")
    if resultado["compartidos_emisor"].empty:
        st.info("No hay emisores compartidos entre estos fondos.")
    else:
        st.dataframe(compartidos(resultado["compartidos_emisor"]), hide_index=True, width="stretch")

    st.subheader("Activos exclusivos de cada fondo")
    for nombre, tabla in resultado["exclusivos"].items():
        with st.expander(f"{nombre} · {len(tabla)} exclusivos", expanded=False):
            if tabla.empty:
                st.caption("Todos sus activos conocidos están en otro de los fondos elegidos.")
            else:
                st.dataframe(formato.tabla(tabla, numeros={"% del patrimonio": 2}), hide_index=True, width="stretch")

    st.subheader("Por tipo de activo, moneda e indexación (% del patrimonio, sin Resto)")
    for titulo, tabla in resultado["dimensiones"].items():
        st.markdown(f"**{titulo}**")
        st.dataframe(formato.tabla(tabla, numeros={f: 2 for f in fondos}), hide_index=True, width="stretch")
