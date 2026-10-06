"""Filas a revisar: rendimientos que difieren de los de la CAFCI (auditoría C-02 y C-03)."""
import streamlit as st

import acceso
import datos
import formato

st.set_page_config(page_title="Filas a revisar", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Filas a revisar")
st.markdown(
    "El robot compara los rendimientos que calcula (diario, mes, año y 12 meses) con los que publica la CAFCI. "
    "Cuando difieren en más de **0,01 punto porcentual**, la fila queda acá. Casi siempre la causa es de la CAFCI: "
    "versiones inconsistentes de una misma planilla (por ejemplo, una versión que repite el valor del día anterior) "
    "o distribuciones de utilidades, que los rendimientos no consideran. No son necesariamente errores del robot.")

dias = st.radio("Mostrar los últimos", [30, 60, 120, 365], index=2, horizontal=True, format_func=lambda d: f"{d} días")
r = datos.filas_a_revisar(dias)
if r.empty:
    st.success(f"No hay filas a revisar en los últimos {dias} días.")
    st.stop()

fondos = ["Todos"] + sorted(r["fondo"].unique())
f = st.selectbox("Fondo", fondos)
if f != "Todos":
    r = r[r["fondo"] == f]
c1, c2 = st.columns(2)
c1.metric("Filas a revisar", len(r))
c2.metric("Fechas distintas", r["fecha"].nunique())
st.dataframe(formato.tabla(r.rename(columns={
    "fondo": "Fondo", "clase": "Clase", "fecha": "Fecha", "diario": "Diario calculado %",
    "variacion_diaria_cafci": "Diario CAFCI %", "diferencia_vs_cafci": "Diferencia diaria (puntos)", "nota": "Detalle"}),
    fechas=["Fecha"], numeros={"Diario calculado %": 3, "Diario CAFCI %": 3, "Diferencia diaria (puntos)": 3}),
    hide_index=True, width="stretch")
