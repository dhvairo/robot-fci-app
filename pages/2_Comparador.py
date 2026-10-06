"""Comparador: evolución y rendimiento de clases de distintos fondos en un período."""
from datetime import timedelta

import plotly.express as px
import streamlit as st

import acceso
import datos

st.set_page_config(page_title="Comparador", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Comparador")

cl = datos.clases_seguidas()
if cl.empty:
    st.info("No hay clases seguidas.")
    st.stop()
cl["etiqueta"] = cl.apply(lambda r: f"{r['fondo']} — {r['clase'].split(' - ')[-1]}", axis=1)

monedas = sorted(cl["moneda"].dropna().unique())
mon = st.radio("Moneda", monedas + ["Todas"], horizontal=True,
               help="Conviene comparar fondos de la misma moneda. ARS = pesos, USD = dólar, "
                    "USB = dólar billete (códigos de la CAFCI).")
base = cl if mon == "Todas" else cl[cl["moneda"] == mon]
pref = [e for e, n in zip(base["etiqueta"], base["clase"]) if " - Clase A" in n or " - Clase B" in n]
elegidas = st.multiselect("Clases a comparar", base["etiqueta"].tolist(), default=pref[:6])
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
per = st.radio("Período", list(periodos), index=1, horizontal=True)
desde = primero if periodos[per] is None else max(primero, ultimo - timedelta(days=periodos[per]))
st.caption(f"Desde {desde:%d/%m/%Y} hasta {ultimo:%d/%m/%Y}")

ser = ser[ser["fecha"] >= desde].copy()
# Cada clase parte de su primer valor dentro del período (base 100).
ser["base 100"] = ser.groupby("codigo_cafci")["cuotaparte"].transform(lambda s: s / s.iloc[0] * 100)
fig = px.line(ser, x="fecha", y="base 100", color="etiqueta", title="Evolución (base 100 al inicio del período)")
fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
st.plotly_chart(fig, use_container_width=True)

res = ser.groupby(["etiqueta", "moneda"]).agg(primera=("fecha", "min"), ultima=("fecha", "max"),
                                              valor_inicial=("cuotaparte", "first"),
                                              valor_final=("cuotaparte", "last"),
                                              observaciones=("fecha", "count")).reset_index()
res["Rendimiento %"] = (res["valor_final"] / res["valor_inicial"] - 1) * 100
res = res.sort_values("Rendimiento %", ascending=False)
st.dataframe(res.rename(columns={"etiqueta": "Clase", "moneda": "Moneda", "primera": "Desde",
                                 "ultima": "Hasta", "observaciones": "Datos"})[
    ["Clase", "Moneda", "Desde", "Hasta", "Datos", "Rendimiento %"]],
    hide_index=True, use_container_width=True,
    column_config={"Rendimiento %": st.column_config.NumberColumn(format="%.2f")})
if (res["primera"] > desde).any():
    st.caption("Algunas clases empiezan después del inicio del período: su rendimiento cubre menos días.")
if sel["moneda"].nunique() > 1:
    st.warning("Estás comparando monedas distintas: los rendimientos no son directamente comparables.")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades. "
           "No es una recomendación de inversión.")
