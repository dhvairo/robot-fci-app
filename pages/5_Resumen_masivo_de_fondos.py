"""Informe descriptivo: la tabla de la solapa "Resumen" del Excel de carteras y el informe (pantalla y PDF)."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402
import filtros  # noqa: E402
import informe  # noqa: E402
import marca  # noqa: E402
import rapidez  # noqa: E402

st.set_page_config(page_title="Informe descriptivo", page_icon="📈", layout="wide")
acceso.requerir()
inicial_seguimiento = calculos.filtro_seguimiento_inicial(st.session_state)
st.title("Informe descriptivo")
st.caption("Composición de la última cartera disponible de los fondos seleccionados (fuente: CNV).")
st.button("Restablecer", on_click=filtros.restablecer_informe, args=(st.session_state,))
transferencia = st.session_state.get(calculos.SELECCION_INFORME, [])
# Un nuevo pase abre el modo comparados; las siguientes corridas conservan los controles propios.
if transferencia and st.session_state.get("informe_transferencia") != transferencia:
    filtros.restablecer_informe(st.session_state)
    st.session_state["informe_transferencia"] = list(transferencia)
    st.session_state["informe_modo"] = "Tomar fondos comparados"
    st.session_state["informe_filtro_0"] = "Todos"
modo = st.radio("Origen de los fondos", ["Tomar fondos comparados", "Búsqueda libre"],
                index=1, key="informe_modo")
todos = datos.fondos_ficha(incluir_no_seguidos=True)
vista = todos.attrs.get('clasificacion')
if modo == "Tomar fondos comparados":
    if not transferencia:
        st.info("No hay selección del Comparador. Elegí fondos allí o usá Búsqueda libre.")
        st.stop()
    todos = todos[todos.codigo_cnv.isin(transferencia)]
if todos.empty:
    st.info("No hay fondos disponibles para este modo.")
    st.stop()
base = filtros.mostrar(todos, clave="informe", moneda_todos="Todos", inicial=inicial_seguimiento)
nombres = dict(zip(base.codigo_cnv, base.nombre))
elegidos = filtros.lista_fondos("Fondos a incluir", nombres, "informe_fondos",
                               nombres if modo == "Tomar fondos comparados" else ())
if not elegidos:
    st.info("Elegí al menos un fondo.")
    st.stop()
sel = base[base.codigo_cnv.isin(elegidos)]
carteras, sumas, tenencias = rapidez.memorizar("resumen_carteras", lambda: datos.resumen_carteras(vista))
sin_cartera = sorted(set(sel["nombre"]) - set(sel[sel["codigo_cnv"].isin(carteras["codigo_cnv"])]["nombre"]))
if sin_cartera:
    st.warning("Todavía no tienen cartera cargada y no se incluyen: " + ", ".join(sin_cartera) + ".")
    sel = sel[~sel["nombre"].isin(sin_cartera)]
    if sel.empty:
        st.stop()
seleccion = tuple(sel.codigo_cnv)
tabla = rapidez.memorizar("resumen_masivo", lambda: calculos.resumen_masivo(sel, carteras, sumas, tenencias), seleccion)

# ───────── La tabla de la solapa "Resumen" ─────────
fechas = sorted({f for f in tabla["Cartera al"] if not formato._es_nulo(f)})
if len(fechas) == 1:
    st.subheader(f"Resumen de carteras de FCI: composición por tipo de activo (cartera al {formato.fecha(fechas[0])})")
else:
    st.subheader("Resumen de carteras de FCI: composición por tipo de activo")
    st.info("Los fondos tienen carteras de fechas distintas: la fecha de cada uno está en la columna \"Cartera al\".")
st.caption("Fuente: CNV, Composición Semanal de Cartera. % sobre patrimonio neto, calculados desde cada cartera.")
columnas = calculos.columnas_resumen(tabla)
porcentajes = [c for c in columnas if c not in ("Patrimonio neto",) and (
    c in {calculos.ETIQUETA_CATEGORIA.get(k, k) for k in calculos.CATEGORIAS_RESUMEN} or c == calculos.AJUSTE
    or c.startswith("% "))]
st.dataframe(rapidez.memorizar("informe_tabla", lambda: formato.tabla(tabla[columnas],
    fechas=["Cartera al"] if "Cartera al" in columnas else (),
    numeros={**{c: 2 for c in porcentajes}, "Patrimonio neto": 2}), seleccion),
    hide_index=True, width="stretch")
st.markdown("**Notas:**\n\n" + "\n".join(f"- {n}" for n in calculos.NOTAS_RESUMEN))

# ───────── El informe: una sección por moneda ─────────
st.divider()
secs = rapidez.memorizar("informe_secciones", lambda: informe.secciones(tabla, fondos=sel), seleccion)
st.header("Informe")
rapidez.pdf_a_pedido("informe", seleccion, lambda: informe.pdf(secs), informe.nombre_archivo(secs, tabla),
                   "Descargar informe en PDF", tipo="primary")
for sec in secs:
    st.subheader(sec["titulo"])
    st.caption(sec["subtitulo"])
    st.caption(sec["aclaracion"])
    fondos = sec["fondos"]
    def armar_torta():
        fig = go.Figure(go.Pie(
            labels=[f["nombre"] for f in fondos], values=[1] * len(fondos), sort=False, direction="clockwise",
            marker=dict(colors=[marca.paleta()[i % len(marca.paleta())] for i in range(len(fondos))],
                        line=dict(color="white", width=2)),
            text=[f["etiqueta"].replace("\n", "<br>") for f in fondos], textinfo="text", textposition="outside",
            hoverinfo="text", showlegend=False))
        fig.update_layout(title=dict(text=sec["titulo_torta"], x=0.5), height=520, margin=dict(t=70, b=30, l=10, r=10))
        return formato.plotly_es(fig)
    st.plotly_chart(rapidez.memorizar("informe_torta", armar_torta, seleccion, sec["titulo"]), width="stretch")
    filas = [{"Fondo": f["nombre"] + (f" (cartera al {f['fecha']})" if f["fecha"] else ""),
              "Principales rubros": "  \n".join(f["rubros"]) or formato.VACIO, "Idea y temática": f["idea"]}
             for f in fondos]
    st.table(pd.DataFrame(filas).set_index("Fondo"))
    st.caption(sec["descargo"])
