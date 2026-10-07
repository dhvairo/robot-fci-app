"""Resumen masivo de fondos: la tabla de la solapa "Resumen" del Excel de carteras y el informe (pantalla y PDF)."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402
import informe  # noqa: E402

st.set_page_config(page_title="Resumen masivo de fondos", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Resumen masivo de fondos")
st.caption("Composición de la última cartera de los fondos que sigue el robot (fuente: CNV). "
           "Los fondos que se siguen se suman o se quitan con pedidos (la pantalla para pedirlos está en preparación).")

todos = datos.fondos_ficha()
if todos.empty:
    st.info("No hay fondos seguidos.")
    st.stop()
nombres = todos["nombre"].tolist()
elegidos = st.multiselect("Fondos a incluir", nombres, default=nombres,
                          help="Elegí los fondos seguidos que quieras: uno, varios o todos.")
if not elegidos:
    st.info("Elegí al menos un fondo.")
    st.stop()
sel = todos[todos["nombre"].isin(elegidos)]
carteras, sumas, tenencias = datos.resumen_carteras()
sin_cartera = sorted(set(sel["nombre"]) - set(sel[sel["codigo_cnv"].isin(carteras["codigo_cnv"])]["nombre"]))
if sin_cartera:
    st.warning("Todavía no tienen cartera cargada y no se incluyen: " + ", ".join(sin_cartera) + ".")
    sel = sel[~sel["nombre"].isin(sin_cartera)]
    if sel.empty:
        st.stop()
tabla = calculos.resumen_masivo(sel, carteras, sumas, tenencias)

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
st.dataframe(formato.tabla(tabla[columnas], fechas=["Cartera al"] if "Cartera al" in columnas else (),
                           numeros={**{c: 2 for c in porcentajes}, "Patrimonio neto": 2}),
             hide_index=True, width="stretch")
st.markdown("**Notas:**\n\n" + "\n".join(f"- {n}" for n in calculos.NOTAS_RESUMEN))

# ───────── El informe: una sección por moneda ─────────
st.divider()
secs = informe.secciones(tabla)
st.header("Informe")
st.download_button("Descargar informe en PDF", data=informe.pdf(secs), file_name=informe.nombre_archivo(secs, tabla),
                   mime="application/pdf", type="primary")
for sec in secs:
    st.subheader(sec["titulo"])
    st.caption(sec["subtitulo"])
    st.caption(sec["aclaracion"])
    fondos = sec["fondos"]
    fig = go.Figure(go.Pie(
        labels=[f["nombre"] for f in fondos], values=[1] * len(fondos), sort=False, direction="clockwise",
        marker=dict(colors=[informe.PALETA[i % len(informe.PALETA)] for i in range(len(fondos))],
                    line=dict(color="white", width=2)),
        text=[f["etiqueta"].replace("\n", "<br>") for f in fondos], textinfo="text", textposition="outside",
        hoverinfo="text", showlegend=False))
    fig.update_layout(title=dict(text=sec["titulo_torta"], x=0.5), height=520, margin=dict(t=70, b=30, l=10, r=10))
    st.plotly_chart(formato.plotly_es(fig), width="stretch")
    filas = [{"Fondo": f["nombre"] + (f" (cartera al {f['fecha']})" if f["fecha"] else ""),
              "Principales rubros": "  \n".join(f["rubros"]) or formato.VACIO, "Idea y temática": f["idea"]}
             for f in fondos]
    st.table(pd.DataFrame(filas).set_index("Fondo"))
    st.caption(sec["descargo"])
