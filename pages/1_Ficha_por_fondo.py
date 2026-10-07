"""Ficha por fondo: clases, rendimientos, evolución, cartera clasificada y hechos relevantes."""
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402

# Colores de la torta; cada uno tiene su cuadradito de color para el botón del mismo rubro.
PALETA = [("🔴", "#E04B4B"), ("🟠", "#F28E2B"), ("🟡", "#EDC948"), ("🟢", "#59A14F"), ("🔵", "#4E79A7"),
          ("🟣", "#B07AA1"), ("🟤", "#9C755F"), ("⚫", "#444444")]
GRIS = ("⚪", "#BAB0AC")

st.set_page_config(page_title="Ficha por fondo", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Ficha por fondo")

todos = datos.fondos_ficha()
if todos.empty:
    st.info("No hay fondos seguidos.")
    st.stop()
c_mon, c_rub, c_fon = st.columns(3)
moneda_op = c_mon.radio("Moneda", calculos.OPCIONES_MONEDA, horizontal=True,
                        help="Dólares incluye USD y USD billete.")
de_moneda = calculos.filtrar_moneda(todos, moneda_op)
if de_moneda.empty:
    st.info(f"No hay fondos seguidos en {moneda_op.lower()}.")
    st.stop()
rubro = c_rub.selectbox("Rubro", sorted(de_moneda["rubro"].dropna().unique()),
                        help="Rubro del fondo según su cartera (las 3 mayores tenencias).")
nombre = c_fon.selectbox("Fondo", de_moneda[de_moneda["rubro"] == rubro]["nombre"])
fondo = de_moneda[de_moneda["nombre"] == nombre].iloc[0]
codigo = int(fondo["codigo_cnv"])

st.subheader(nombre)
cab = calculos.cabecera_fondo(fondo.to_dict())
for i in range(0, len(cab), 3):
    for col, (etiqueta, valor) in zip(st.columns(3), cab[i:i + 3]):
        col.markdown(f"**{etiqueta}**  \n{valor}")
descripcion, idea = calculos.tematica(fondo.to_dict())
st.markdown(f"**Descripción breve:** {descripcion}")
st.markdown(f"**Idea y temática:** {idea}")
st.caption(f"Código CNV {codigo} · {fondo['estado']}")

# ───────── Clases y rendimientos ─────────
clases = datos.clases_de_fondo(codigo)
st.subheader("Clases")
if clases.empty:
    st.info("El fondo no tiene clases vigentes en el catálogo.")
else:
    por_defecto = datos.clases_preferidas(clases)
    elegidas = st.multiselect("Clases a mostrar", clases["nombre"].tolist(),
                              default=por_defecto["nombre"].tolist(),
                              help="Por defecto Clase A y B; si el fondo no las tiene, las disponibles.")
    sel = clases[clases["nombre"].isin(elegidas)]
    if len(sel):
        rend = datos.rendimientos_ultimos(sel["codigo_cafci"])
        tabla = sel.merge(rend, on="codigo_cafci", how="left")
        revisar = tabla[tabla["revisar"] == True]["nombre"].tolist()  # noqa: E712
        tabla = tabla.rename(columns={"nombre": "Clase", "moneda": "Moneda", "fecha": "Fecha",
                                      "cuotaparte": "Cuotaparte", "patrimonio": "Patrimonio",
                                      "diario": "Diario %", "dias_7": "7 días %", "dias_30": "30 días %",
                                      "mes": "Mes %", "anio": "Año %", "meses_12": "12 meses %"})
        cols = ["Clase", "Moneda", "Fecha", "Cuotaparte", "Patrimonio", "Diario %", "7 días %",
                "30 días %", "Mes %", "Año %", "12 meses %"]
        st.dataframe(formato.tabla(tabla[cols], fechas=["Fecha"], enteros=["Patrimonio"],
                                   numeros={"Cuotaparte": 4, **{c: 2 for c in cols if c.endswith("%")}}),
                     hide_index=True, width="stretch")
        st.caption("Rendimientos calculados por el robot sobre la cuotaparte; \"—\" = historia insuficiente (o clase "
                   "sin patrimonio) para ese período. No consideran distribución de utilidades.")
        if revisar:
            st.warning("Diario, mes, año o 12 meses calculados distintos de los de la CAFCI en: " + ", ".join(revisar)
                       + ". Ver la pantalla \"Filas a revisar\".")

        ser = datos.serie(sel["codigo_cafci"]).merge(sel[["codigo_cafci", "nombre"]], on="codigo_cafci")
        if ser["fecha"].nunique() < 2:
            st.info("Todavía hay una sola fecha de cuotaparte: el gráfico aparece cuando se cargue la historia.")
        else:
            ser["base 100"] = ser.groupby("codigo_cafci")["cuotaparte"].transform(
                lambda s: s / s.iloc[0] * 100)
            fig = px.line(ser, x="fecha", y="base 100", color="nombre",
                          title="Evolución de la cuotaparte (base 100 en la primera fecha)")
            fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
            st.plotly_chart(formato.plotly_es(fig, fechas_x=True), width="stretch")

# ───────── Cartera ─────────
st.subheader("Cartera semanal")
enc = datos.cartera_encabezado(codigo)
cart = datos.cartera(codigo)
if enc.empty or cart.empty:
    st.info("Todavía no hay cartera cargada para este fondo.")
else:
    e = enc.iloc[0]
    st.caption(f"Cartera al {formato.fecha(e['fecha_cartera'])} · patrimonio {formato.entero(e['patrimonio'])} "
               f"{e['moneda']} · {int(e['instrumentos'])} líneas")
    if int(e["sin_clasificar"]):
        st.warning(f"{int(e['sin_clasificar'])} línea(s) sin clasificar ({formato.numero(e['pct_sin_clasificar'])}% del PN).")
    activos = cart[cart["categoria_resumen"] != "Pasivos"]
    pasivos = cart[cart["categoria_resumen"] == "Pasivos"]["pct_pn"].sum()
    g1, g2 = st.columns(2)
    resumen = activos.groupby("categoria_resumen", as_index=False)["pct_pn"].sum()
    resumen = resumen[resumen["pct_pn"] > 0].sort_values("pct_pn", ascending=False).reset_index(drop=True)
    marcas = [PALETA[i] if i < len(PALETA) else GRIS for i in range(len(resumen))]
    etiquetas = {r: f"{m[0]} {r} · {formato.numero(p)}%"
                 for m, r, p in zip(marcas, resumen["categoria_resumen"], resumen["pct_pn"])}
    with g1:
        # Streamlit no deja tocar porciones de una torta: los rubros se eligen con los botones de abajo.
        elegidos = [r for r in (st.session_state.get(f"rubros_{codigo}") or []) if r in etiquetas]
        colores = [m[1] if (not elegidos or r in elegidos) else m[1] + "40"
                   for m, r in zip(marcas, resumen["categoria_resumen"])]
        fig = go.Figure(go.Pie(labels=resumen["categoria_resumen"], values=resumen["pct_pn"], hole=0.4, sort=False,
                               marker=dict(colors=colores),
                               pull=[0.08 if r in elegidos else 0 for r in resumen["categoria_resumen"]]))
        fig.update_layout(title="Por rubro", margin=dict(t=50, b=10))
        st.plotly_chart(formato.plotly_es(fig), width="stretch")
    det = activos.groupby("categoria_detallada", as_index=False)["pct_pn"].sum()
    det = det[det["pct_pn"] > 0].sort_values("pct_pn")
    g2.plotly_chart(formato.plotly_es(px.bar(det, x="pct_pn", y="categoria_detallada", orientation="h",
                                             title="Por rubro, moneda e indexación (% del PN)",
                                             labels={"pct_pn": "% del PN", "categoria_detallada": ""})),
                    width="stretch")
    st.pills("Rubros (tocá uno o más para ver solo sus instrumentos; tocá de nuevo para sacarlo)",
             list(etiquetas), format_func=etiquetas.get, selection_mode="multi", key=f"rubros_{codigo}")
    st.caption(f"Pasivos (implícitos): {formato.numero(pasivos)}% del patrimonio. Se muestran aparte porque restan.")
    dif = 100 - float(cart["pct_pn"].sum())
    if abs(dif) > 0.05:
        st.caption(f"Las líneas suman {formato.numero(100 - dif)}%: la diferencia de {formato.numero(dif)} puntos es "
                   "redondeo de la CNV (cada renglón viene redondeado a 2 decimales).")
    filtrada = calculos.filtrar_por_rubros(activos, elegidos)
    top = filtrada.sort_values("pct_pn", ascending=False).head(10)
    st.markdown("**10 mayores tenencias**" + (f" de {', '.join(elegidos)}" if elegidos else ""))
    st.dataframe(formato.tabla(top[["instrumento", "pct_pn", "categoria_detallada"]].rename(
        columns={"instrumento": "Instrumento", "pct_pn": "% del PN", "categoria_detallada": "Categoría"}),
        numeros={"% del PN": 2}), hide_index=True, width="stretch")
    with st.expander("Cartera completa"):
        st.dataframe(formato.tabla(calculos.filtrar_por_rubros(cart, elegidos).drop(columns=["orden"]).rename(columns={
            "rubro_cnv": "Rubro CNV", "instrumento": "Instrumento", "pct_pn": "% del PN",
            "categoria_resumen": "Rubro", "categoria_detallada": "Categoría", "moneda": "Moneda",
            "indexacion": "Indexación", "sin_clasificar": "Sin clasificar"}),
            numeros={"% del PN": 2}, booleanos=["Sin clasificar"]), hide_index=True, width="stretch")

# ───────── Hechos relevantes ─────────
st.subheader("Hechos relevantes")
h = datos.hechos(codigo)
if h.empty:
    st.info("Sin hechos relevantes registrados.")
else:
    tipos = sorted(t for t in h["tipo"].unique() if t)
    filtro = st.multiselect("Filtrar por tipo", tipos)
    if filtro:
        h = h[h["tipo"].isin(filtro)]
    st.dataframe(formato.tabla(h.rename(columns={"fecha": "Fecha", "tipo": "Tipo", "descripcion": "Descripción",
                                                 "link": "Documento"}), fechas=["Fecha"]),
                 hide_index=True, width="stretch",
                 column_config={"Documento": st.column_config.LinkColumn("Documento", display_text="Abrir")})
