"""Robot FCI - pantalla de inicio: estado de la base y del robot.

Se abre con:  streamlit run app/Inicio.py   (o con "Abrir app.bat")
"""
import pandas as pd
import streamlit as st

import acceso
import datos

st.set_page_config(page_title="Robot FCI", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Robot FCI")
st.caption("Base de datos propia de fondos comunes de inversión argentinos. Solo lectura.")

r = datos.resumen_base()


def miles(n):
    return f"{int(n):,}".replace(",", ".")


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
    st.dataframe(e, hide_index=True, use_container_width=True)

st.markdown("""
**Pantallas** (menú de la izquierda):
- **Ficha por fondo:** clases, rendimientos, evolución de la cuotaparte, cartera y hechos relevantes.
- **Comparador:** compara clases de distintos fondos en un período.
- **Buscador de instrumentos:** en qué fondos seguidos está un instrumento y con qué peso.
""")
st.caption("Los rendimientos no consideran distribución de utilidades. Esta herramienta informa; "
           "no es una recomendación de inversión.")
