"""Buscador de instrumentos: en qué fondos seguidos está y con qué peso."""
import plotly.express as px
import streamlit as st

import acceso
import datos
import formato

st.set_page_config(page_title="Buscador de instrumentos", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Buscador de instrumentos")
st.caption("Busca en la última cartera semanal de cada fondo seguido. Ejemplos: YPF, Pampa, Boncer, Megabono, Cheque.")

texto = st.text_input("Instrumento (parte del nombre)", placeholder="YPF")
if len(texto.strip()) < 2:
    st.info("Escribí al menos 2 letras.")
    st.stop()
r = datos.buscar_instrumentos(texto.strip())
if r.empty:
    st.warning("No se encontró en las carteras de los fondos seguidos.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Líneas encontradas", len(r))
c2.metric("Fondos que lo tienen", r["fondo"].nunique())
c3.metric("Mayor peso", f"{formato.numero(r['pct_pn'].max())}% del PN")

por_fondo = r.groupby("fondo", as_index=False)["pct_pn"].sum().sort_values("pct_pn")
st.plotly_chart(formato.plotly_es(px.bar(por_fondo, x="pct_pn", y="fondo", orientation="h",
                                         labels={"pct_pn": "% del PN (suma de coincidencias)", "fondo": ""})),
                width="stretch")
st.dataframe(formato.tabla(r.rename(columns={"fondo": "Fondo", "instrumento": "Instrumento", "pct_pn": "% del PN",
                                             "categoria_resumen": "Rubro", "categoria_detallada": "Categoría",
                                             "moneda": "Moneda", "fecha_cartera": "Cartera al"}),
                           fechas=["Cartera al"], numeros={"% del PN": 2}),
             hide_index=True, width="stretch")
