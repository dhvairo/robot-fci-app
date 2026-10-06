"""Robot FCI - pantalla de inicio: estado de la base y del robot.

Se abre con:  streamlit run app/Inicio.py   (o con "Abrir app.bat")
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import acceso
import datos
import estado
import formato

st.set_page_config(page_title="Robot FCI", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Robot FCI")
st.caption("Base de datos propia de fondos comunes de inversión argentinos. Solo lectura.")

r = datos.resumen_base()


def miles(n):
    return f"{int(n):,}".replace(",", ".")


# Semáforo: ¿están llegando datos nuevos? (hora de Argentina = UTC-3)
hoy = datetime.now(timezone(timedelta(hours=-3))).date()
for nivel, mensaje in estado.semaforo(hoy, r.hasta if pd.notna(r.hasta) else None,
                                      r.ultima_cartera if pd.notna(r.ultima_cartera) else None):
    {"ok": st.success, "aviso": st.warning, "error": st.error}[nivel](mensaje)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Fondos seguidos", f"{int(r.seguidos)} de {miles(r.fondos)}")
c2.metric("Clases en el catálogo", miles(r.clases))
c3.metric("Último valor diario", f"{r.hasta:%d/%m/%Y}" if pd.notna(r.hasta) else "—",
          help="Fecha más reciente de cuotaparte guardada")
c4.metric("Última cartera", f"{r.ultima_cartera:%d/%m/%Y}" if pd.notna(r.ultima_cartera) else "—",
          help="Cartera semanal más reciente (cierre del viernes)")

c5, c6, c7, c8 = st.columns(4)
c5.metric("Valores diarios guardados", miles(r.valores))
c6.metric("Historia desde", f"{r.desde:%d/%m/%Y}" if pd.notna(r.desde) else "—")
c7.metric("Hechos relevantes", int(r.hechos))
c8.metric("Líneas sin clasificar", int(r.sin_clasificar),
          help="Instrumentos de las carteras sin regla de clasificación (config/reglas_clasificacion.csv)")
if int(r.sin_clasificar):
    st.warning("Hay instrumentos sin clasificar: agregá una regla en config/reglas_clasificacion.csv.")

st.subheader("Últimos archivos procesados por el robot")
e = datos.estado_robot()
if e.empty:
    st.info("Todavía no hay archivos procesados.")
else:
    e = e.rename(columns={"fuente": "Fuente", "nombre_archivo": "Archivo", "fecha_dato": "Fecha del dato",
                          "version": "Versión", "filas": "Filas", "controles_ok": "Controles OK",
                          "recibido_en": "Recibido"})
    st.dataframe(formato.tabla(e, fechas=["Fecha del dato"], enteros=["Filas"], booleanos=["Controles OK"],
                               fechas_hora_ar=["Recibido"]), hide_index=True, width="stretch")
    st.caption("Horas en hora de Argentina.")

st.markdown("""
**Pantallas** (menú de la izquierda):
- **Ficha por fondo:** clases, rendimientos, evolución de la cuotaparte, cartera y hechos relevantes.
- **Comparador:** compara clases de distintos fondos en un período.
- **Buscador de instrumentos:** en qué fondos seguidos está un instrumento y con qué peso.
- **Filas a revisar:** rendimientos que difieren de los de la CAFCI.
""")
st.caption("Los rendimientos no consideran distribución de utilidades. Esta herramienta informa; "
           "no es una recomendación de inversión.")
