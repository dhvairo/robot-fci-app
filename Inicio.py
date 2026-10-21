"""Robot FCI - pantalla de inicio: estado de la base y del robot.

Se abre con:  streamlit run app/Inicio.py   (o con "Abrir app.bat")
"""
import time
import logging
_inicio_rerun = time.perf_counter()
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")
recarga.al_dia(("operacion", "navegacion"))

import acceso  # noqa: E402
import marca  # noqa: E402
import datos  # noqa: E402
import estado  # noqa: E402
import operacion  # noqa: E402
import formato  # noqa: E402
import horarios  # noqa: E402
import navegacion  # noqa: E402
import estilo  # noqa: E402

st.set_page_config(page_title=marca.nombre(), page_icon="📈", layout="wide")


def mostrar_inicio():
    acceso.requerir()
    st.title("Inicio")
    st.caption("Base de datos propia de fondos comunes de inversión argentinos. Solo lectura: lo único que la app puede hacer es anotar pedidos de seguir o dejar de seguir fondos.")

    st.subheader("Última ejecución del robot")
    op = operacion.obtener(st.secrets)
    for nombre, ejecucion in op["flujos"].items():
        {"ok": st.success, "aviso": st.warning, "error": st.error}[ejecucion["nivel"]](
            f"Robot {nombre}: {ejecucion['estado']}. Inicio de última ejecución (Argentina): {ejecucion['fecha']}")
        st.link_button(f"Ver Actions: {nombre}", ejecucion["enlace"])
    st.caption(f"Consulta: {op['consultado']:%d/%m/%Y %H:%M:%S}. "
               f"Caché compartida hasta {op['vence']:%d/%m/%Y %H:%M:%S} (Argentina; 10 minutos). "
               "El estado de ejecución es independiente de la antigüedad del dato. "
               "Los enlaces a Actions requieren acceso al repositorio privado.")

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

    st.subheader("Cuándo se actualiza cada dato")
    ahora = datetime.now(horarios.ARGENTINA)
    u = datos.ultimas_entradas()
    cuadro = pd.DataFrame(
        horarios.filas_cuadro(horarios.cargar(), ahora, {k: u[k] for k in u.index}, formato.fecha_hora_ar),
        columns=["Dato", "Cuándo se actualiza", "Última vez que entró un dato nuevo", "Próxima corrida programada"])
    st.dataframe(formato.tabla(cuadro), hide_index=True, width="stretch")   # ya viene en formato argentino
    st.caption("Hora de Argentina. Los horarios salen de los flujos del robot en GitHub (GitHub puede demorar el inicio "
               "unos minutos). La CNV publica las carteras con 2-3 semanas de demora. La foto de todas las clases trae "
               "rendimientos, patrimonio y calificación de cada clase del catálogo. Si una planilla no trae datos nuevos, "
               "no se guarda nada.")

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
    - **Informe descriptivo:** tabla e informe (pantalla y PDF) con la composición de los fondos seguidos que elijas.
    - **Gestión y eficiencia:** costos, patrimonio y rendimiento de todas las clases, con un ranking informativo.
    - **Sumar o quitar fondos:** pedirle al robot que siga (o deje de seguir) un fondo del catálogo, y ver el estado de los pedidos.
    """)
    st.caption("Los rendimientos no consideran distribución de utilidades. Esta herramienta informa; "
               "no es una recomendación de inversión.")


try:
    _hoja_estilo = st.empty()
    estilo.presentar(_hoja_estilo, lambda: navegacion.mostrar(mostrar_inicio))
finally:
    _cache_rerun = acceso._estado_rerun() or {}
    logging.getLogger('seguimiento').log(logging.WARNING,
        'SEGUIMIENTO TIEMPO RERUN_TOTAL %.3f_ms cache_aciertos=%d cache_fallos=%d',
        (time.perf_counter() - _inicio_rerun) * 1000,
        _cache_rerun.get('cache_aciertos', 0), _cache_rerun.get('cache_fallos', 0))
