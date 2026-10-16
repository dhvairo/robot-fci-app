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

st.set_page_config(page_title="Comparador", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Comparador")

st.button("Restablecer", on_click=filtros.restablecer_comparador, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
if todos.empty:
    st.info("No hay fondos en el catálogo.")
    st.stop()
base = filtros.mostrar(todos, clave="comparador", persistente=True)
if base.empty:
    st.info("No hay fondos para estos filtros.")
    st.stop()
nombres = base.set_index("codigo_cnv")["nombre"].to_dict()
with st.expander("Fondos", expanded=False):
    def guardar_busqueda():
        st.session_state["comparador_busqueda_memoria"] = st.session_state["comparador_busqueda"]

    if "comparador_busqueda" not in st.session_state:
        st.session_state["comparador_busqueda"] = st.session_state.get("comparador_busqueda_memoria", "")
    texto = st.text_input("Buscar por denominación", key="comparador_busqueda", on_change=guardar_busqueda)
    visibles = filtros.buscar_denominacion(base, texto)
    if visibles.empty:
        st.info("No hay fondos para esta búsqueda.")
    elegidos = filtros.casillas("Fondos", base.codigo_cnv.tolist(), "comparador_fondos",
                               formato=nombres.get, persistente=True, visibles=visibles.codigo_cnv.tolist())
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
if st.button("Ver estos fondos en el Informe descriptivo"):
    calculos.enviar_al_informe(st.session_state, fondos.codigo_cnv)
    st.switch_page("pages/5_Resumen_masivo_de_fondos.py")

cache = st.session_state.setdefault("consulta_puntual", {})
def cargar(fondo):
    with st.spinner(f"Cargando… {fondo['nombre']}"):
        return consulta_puntual.cargar(fondo, cache, consulta_puntual.token_configurado(st.secrets))

ser, notas = calculos.historia_comparador(fondos, sel, datos.serie, cargar)
if (~fondos.seguido).any():
    notas.insert(0, consulta_puntual.LEYENDA)
graficos = []
serie_pdf = ser
mostrar = sel[["etiqueta", "moneda"]].rename(columns={"etiqueta": "Clase", "moneda": "Moneda"})
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
    serie_pdf, resumen, graficos = calculos.comparar(ser, desde, hasta, modo)
    mostrar = mostrar.merge(resumen, on=["Clase", "Moneda"], how="left")
    for fig in graficos:
        st.plotly_chart(formato.plotly_es(fig, fechas_x=True), width="stretch")
    sin_cobertura = calculos.clases_sin_cobertura(serie_pdf, desde, hasta)
    sin_periodo = set(sel.etiqueta) - set(serie_pdf.etiqueta)
    if sin_periodo:
        notas.append("Sin datos en el período: " + ", ".join(sorted(sin_periodo)) + ".")
    if sin_cobertura:
        notas.append("No tienen datos en todo el período: " + ", ".join(sin_cobertura) + ".")
if sel.moneda.nunique() > 1:
    notas.append("Estás comparando monedas distintas: los rendimientos y patrimonios no son directamente comparables.")
if modo in ("P", "R y P") and (ser.empty or ser.patrimonio.isna().any()):
    notas.append("Patrimonio no disponible en algunas fechas o clases; los huecos no se interpolan.")
for nota in notas:
    st.warning(nota)
tabla = formato.tabla(mostrar, fechas=[c for c in ("Desde", "Hasta", "Fecha del patrimonio") if c in mostrar],
                      enteros=[c for c in ("Datos", "Último valor de patrimonio") if c in mostrar],
                      numeros={"Rendimiento %": 2} if "Rendimiento %" in mostrar else {})
st.dataframe(tabla, hide_index=True, width="stretch")
st.download_button("Descargar comparador en PDF",
                   data=informe.pdf_comparador(serie_pdf, tabla, modo, notas),
                   file_name="comparador.pdf", mime="application/pdf")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades.")
st.caption(marca.actual()["texto_legal"])
