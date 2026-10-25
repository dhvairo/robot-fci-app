"""Buscador de instrumentos: en qué fondos seguidos está y con qué peso."""
import plotly.express as px
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402
import filtros  # noqa: E402

st.set_page_config(page_title="Buscador de instrumentos", page_icon="📈", layout="wide")
acceso.requerir()
inicial_seguimiento = calculos.filtro_seguimiento_inicial(st.session_state)
st.title("Buscador de instrumentos")
st.caption("Busca en la última cartera semanal disponible de los fondos seleccionados. Ejemplos: YPF, Pampa, Boncer, Megabono, Cheque.")

catalogo = filtros.situacion(datos.fondos_ficha(incluir_no_seguidos=True), "buscador_situacion", "Seguidos", inicial=inicial_seguimiento)
leyenda = calculos.leyenda_cartera(catalogo.get('fuente_cartera', []))
if leyenda:
    st.caption(leyenda)
st.caption("Los fondos sin cartera disponible no aportan resultados a la búsqueda.")
texto = st.text_input("Instrumento (parte del nombre)", placeholder="YPF")
if len(texto.strip()) < 2:
    st.info("Escribí al menos 2 letras.")
    st.stop()
r = datos.buscar_instrumentos(texto.strip())
r = r[r.fondo.isin(catalogo.nombre)]
if r.empty:
    st.warning("No se encontró en las carteras disponibles de los fondos seleccionados.")
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
