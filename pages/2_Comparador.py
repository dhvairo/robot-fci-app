"""Comparador: evolución y rendimiento de clases de distintos fondos en un período."""
from datetime import timedelta

import plotly.express as px
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402

st.set_page_config(page_title="Comparador", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Comparador")

cl = datos.clases_seguidas()
if cl.empty:
    st.info("No hay clases seguidas.")
    st.stop()
cl["etiqueta"] = cl.apply(lambda r: f"{r['fondo']} — {r['clase'].split(' - ')[-1]}", axis=1)

mon = st.radio("Moneda", ["Todos", *calculos.OPCIONES_MONEDA], horizontal=True,
               help="Conviene comparar fondos de la misma moneda. Dólares incluye USD y USD billete.")
base = calculos.filtrar_moneda(cl, mon)
if base.empty:
    st.info(f"No hay clases seguidas en {mon.lower()}.")
    st.stop()
pref = [e for e, n in zip(base["etiqueta"], base["clase"]) if " - Clase A" in n or " - Clase B" in n]
# Clase A y B de cada fondo; si en esta moneda ningún fondo las tiene, las clases que haya (como en la ficha).
por_defecto = pref[:6] or base["etiqueta"].tolist()[:6]
elegidas = st.multiselect("Clases a comparar", base["etiqueta"].tolist(), default=por_defecto)
if not elegidas:
    st.info("Elegí al menos una clase.")
    st.stop()
sel = base[base["etiqueta"].isin(elegidas)]
ser = datos.serie(sel["codigo_cafci"]).merge(sel[["codigo_cafci", "etiqueta", "moneda"]], on="codigo_cafci")
if ser.empty or ser["fecha"].nunique() < 2:
    st.info("Todavía no hay suficiente historia de cuotapartes (se completa con la carga histórica).")
    st.stop()

ultimo, primero = ser["fecha"].max(), ser["fecha"].min()
periodos = {"7 días": 7, "30 días": 30, "90 días": 90, "6 meses": 182, "12 meses": 365, "Todo": None}
per = st.radio("Período", [*periodos, "Desde / Hasta"], index=1, horizontal=True)
if per == "Desde / Hasta":
    c1, c2 = st.columns(2)
    desde = c1.date_input("Desde", value=max(primero, ultimo - timedelta(days=30)), min_value=primero,
                          max_value=ultimo, format="DD/MM/YYYY")
    hasta = c2.date_input("Hasta", value=ultimo, min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
    aviso = calculos.validar_periodo(desde, hasta)
    if aviso:
        st.warning(aviso)
        st.stop()
else:
    desde = primero if periodos[per] is None else max(primero, ultimo - timedelta(days=periodos[per]))
    hasta = ultimo
st.caption(f"Desde {formato.fecha(desde)} hasta {formato.fecha(hasta)}")

# Cada clase parte de su primer valor dentro del período (base 100).
ser = calculos.base_100(ser, desde, hasta)
if ser.empty:
    st.info("No hay datos de cuotapartes en ese período.")
    st.stop()
fig = px.line(ser, x="fecha", y="base 100", color="etiqueta", title="Evolución (base 100 al inicio del período)")
fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
st.plotly_chart(formato.plotly_es(fig, fechas_x=True), width="stretch")

res = ser.groupby(["etiqueta", "moneda"]).agg(primera=("fecha", "min"), ultima=("fecha", "max"),
                                              valor_inicial=("cuotaparte", "first"),
                                              valor_final=("cuotaparte", "last"),
                                              observaciones=("fecha", "count")).reset_index()
res["Rendimiento %"] = (res["valor_final"] / res["valor_inicial"] - 1) * 100
res = res.sort_values("Rendimiento %", ascending=False)
mostrar = res.rename(columns={"etiqueta": "Clase", "moneda": "Moneda", "primera": "Desde",
                              "ultima": "Hasta", "observaciones": "Datos"})[
    ["Clase", "Moneda", "Desde", "Hasta", "Datos", "Rendimiento %"]]
st.dataframe(formato.tabla(mostrar, fechas=["Desde", "Hasta"], enteros=["Datos"], numeros={"Rendimiento %": 2}),
             hide_index=True, width="stretch")
sin_cobertura = calculos.clases_sin_cobertura(ser, desde, hasta)
if sin_cobertura:
    st.warning("No tienen datos en todo el período (su rendimiento cubre menos días): " + ", ".join(sin_cobertura) + ".")
if sel["moneda"].nunique() > 1:
    st.warning("Estás comparando monedas distintas: los rendimientos no son directamente comparables.")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades. "
           "No es una recomendación de inversión.")
