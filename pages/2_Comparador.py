"""Comparador: filtros comunes, selección por casillas, R/P y PDF."""
from datetime import timedelta

import pandas as pd
import streamlit as st

import recarga
recarga.al_dia()

import acceso
import calculos
import consulta_puntual
import datos
import filtros
import formato
import informe
import marca
import rapidez

st.set_page_config(page_title="Comparador", page_icon="📈", layout="wide")
acceso.requerir()
inicial_seguimiento = calculos.filtro_seguimiento_inicial(st.session_state)
st.title("Comparador")

st.button("Restablecer", on_click=filtros.restablecer_comparador, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
if todos.empty:
    st.info("No hay fondos en el catálogo.")
    st.stop()
base = filtros.mostrar(todos, clave="comparador", persistente=True, inicial=inicial_seguimiento, multiple=True)
if base.empty:
    st.info("No hay fondos para estos filtros.")
    st.stop()
nombres = base.set_index("codigo_cnv")["nombre"].to_dict()
with st.expander("Fondos", expanded=False):
    elegidos = filtros.lista_fondos("Fondos", nombres, "comparador_fondos")
if not elegidos:
    with st.expander("Clase", expanded=False):
        st.info("Seleccioná fondos para ver sus clases.")
    st.info("Elegí al menos un fondo o marcá Todos.")
    st.stop()
fondos = base[base.codigo_cnv.isin(elegidos)]
clases = []
for f in fondos.to_dict("records"):
    cl = datos.clases_de_fondo(int(f["codigo_cnv"])).copy()
    cl = calculos.filtrar_moneda(cl, st.session_state["comparador_filtro_1"]).copy()
    cl["codigo_cnv"] = int(f["codigo_cnv"])
    cl["etiqueta"] = f["nombre"] + " — " + cl["nombre"].str.split(" - ").str[-1]
    clases.append(cl)
clases = pd.concat(clases, ignore_index=True)
if clases.empty:
    st.info("No hay clases disponibles para estos fondos y moneda.")
    st.stop()
preferidas = []
for _, grupo in clases.groupby("codigo_cnv"):
    preferidas += datos.clases_preferidas(grupo).codigo_cafci.tolist()
etiquetas = clases.set_index("codigo_cafci").etiqueta.to_dict()
with st.expander("Clase", expanded=False):
    elegidas = filtros.casillas("Clase", clases.codigo_cafci.tolist(), "comparador_clases",
                               todas="Todas", formato=etiquetas.get, preferidas=preferidas, persistente=True)
if not elegidas:
    st.info("Elegí al menos una clase.")
    st.stop()
sel = clases[clases.codigo_cafci.isin(elegidas)]
fondos = fondos[fondos.codigo_cnv.isin(sel.codigo_cnv)]
modo = st.radio("Qué comparar", ["R", "P", "R y P"], horizontal=True,
                format_func=lambda x: {"R": "Rendimiento (R)", "P": "Patrimonio (P)", "R y P": "R y P"}[x])
if 'comparador_llevar' not in st.session_state:
    st.session_state['comparador_llevar'] = st.session_state.get('comparador_llevar_memoria', False)
llevar = st.toggle("Llevar estos fondos al Informe descriptivo", key="comparador_llevar")
st.session_state['comparador_llevar_memoria'] = llevar
if llevar:
    calculos.enviar_al_informe(st.session_state, fondos.codigo_cnv)

cache = st.session_state.setdefault("consulta_puntual", {})
def cargar(fondo):
    with st.spinner(f"Cargando… {fondo['nombre']}"):
        return consulta_puntual.cargar(fondo, cache, consulta_puntual.token_configurado(st.secrets))

seleccion = (tuple(fondos.codigo_cnv), tuple(sel.codigo_cafci))
if fondos.seguido.all():     # los no seguidos se cargan en la sesión (consulta puntual): no se memorizan
    ser, notas = rapidez.memorizar("comparador_historia", lambda: calculos.historia_comparador(
        fondos, sel, datos.serie, cargar), *seleccion)
    notas = list(notas)
else:
    ser, notas = calculos.historia_comparador(fondos, sel, datos.serie, cargar)
desde = hasta = per = None
if (~fondos.seguido).any():
    notas.insert(0, consulta_puntual.LEYENDA)
graficos = []
serie_pdf = ser
controles = calculos.controles_series(st.session_state, sel)
visibles = sel[sel.codigo_cafci.isin([c["codigo"] for c in controles if c["visible"]])]
serie_pdf = ser[ser.codigo_cafci.isin(visibles.codigo_cafci)] if not ser.empty else ser
mostrar = visibles[["etiqueta", "moneda"]].rename(columns={"etiqueta": "Clase", "moneda": "Moneda"})
st.caption("Series del gráfico: tocá un botón para ocultar o mostrar su línea.")
for control in controles:
    st.button(control["nombre"], key=f"comparador_serie_{control['codigo']}",
              help="Encendida" if control["visible"] else "Apagada",
              on_click=calculos.alternar_serie, args=(st.session_state, control["codigo"]))
if ser.empty:
    notas.append("Sin historia disponible para esta selección.")
else:
    ultimo, primero = ser.fecha.max(), ser.fecha.min()
    periodos = {"7 días": 7, "30 días": 30, "90 días": 90, "6 meses": 182, "12 meses": 365, "Todo": None}
    per = st.radio("Período", [*periodos, "Desde / Hasta"], index=1, horizontal=True)
    if per == "Desde / Hasta":
        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", value=max(primero, ultimo - timedelta(days=30)),
                              min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
        hasta = c2.date_input("Hasta", value=ultimo, min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
        aviso = calculos.validar_periodo(desde, hasta)
        if aviso:
            st.warning(aviso)
            st.stop()
    else:
        desde = primero if periodos[per] is None else max(primero, ultimo - timedelta(days=periodos[per]))
        hasta = ultimo
    st.caption(f"Desde {formato.fecha(desde)} hasta {formato.fecha(hasta)}")
    serie_pdf, resumen, graficos = rapidez.memorizar(
        "comparador_comparar", lambda: calculos.comparar(ser, desde, hasta, modo, controles),
        *seleccion, str(desde), str(hasta), modo, tuple((c["codigo"], c["visible"]) for c in controles),
        bool(fondos.seguido.all()))
    serie_pdf = serie_pdf[serie_pdf.codigo_cafci.isin(visibles.codigo_cafci)]
    mostrar = mostrar.merge(resumen, on=["Clase", "Moneda"], how="left")
    for fig in graficos:
        st.plotly_chart(formato.plotly_es(fig, fechas_x=True), width="stretch")
    sin_cobertura = calculos.clases_sin_cobertura(serie_pdf, desde, hasta)
    sin_periodo = set(visibles.etiqueta) - set(serie_pdf.etiqueta)
    if sin_periodo:
        notas.append("Sin datos en el período: " + ", ".join(sorted(sin_periodo)) + ".")
    if sin_cobertura:
        notas.append("No tienen datos en todo el período: " + ", ".join(sin_cobertura) + ".")
aviso_moneda = calculos.aviso_monedas(visibles.moneda)
if aviso_moneda:
    notas.append(aviso_moneda)
if modo in ("P", "R y P") and (ser.empty or ser.patrimonio.isna().any()):
    notas.append("Patrimonio no disponible en algunas fechas o clases; los huecos no se interpolan.")
for nota in notas:
    st.warning(nota)
tabla = formato.tabla(mostrar, fechas=[c for c in ("Desde", "Hasta", "Fecha del patrimonio") if c in mostrar],
                      enteros=[c for c in ("Datos", "Último valor de patrimonio") if c in mostrar],
                      numeros={"Rendimiento %": 2} if "Rendimiento %" in mostrar else {})
st.dataframe(tabla, hide_index=True, width="stretch")
rapidez.pdf_a_pedido(
    "comparador", (*seleccion, modo, str(desde), str(hasta), tuple((c["codigo"], c["visible"]) for c in controles),
                   tuple(notas)),
    lambda: informe.pdf_comparador(serie_pdf, tabla, modo, notas, fondos=fondos[fondos.codigo_cnv.isin(visibles.codigo_cnv)]),
    "comparador.pdf", "Descargar comparador en PDF")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades.")
st.caption(marca.actual()["texto_legal"])
