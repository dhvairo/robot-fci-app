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
import filtros  # noqa: E402
import informe  # noqa: E402
import consulta_puntual  # noqa: E402
import pandas as pd

# Colores de la torta; cada uno tiene su cuadradito de color para el botón del mismo rubro.
PALETA = [("🔴", "#E04B4B"), ("🟠", "#F28E2B"), ("🟡", "#EDC948"), ("🟢", "#59A14F"), ("🔵", "#4E79A7"),
          ("🟣", "#B07AA1"), ("🟤", "#9C755F"), ("⚫", "#444444")]
GRIS = ("⚪", "#BAB0AC")

st.set_page_config(page_title="Ficha por fondo", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Ficha por fondo")

st.button("Restablecer", on_click=filtros.restablecer_ficha, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
if todos.empty:
    st.session_state["ficha_fondo"] = None
    st.info("No hay fondos en el catálogo.")
    st.stop()
filtrados = filtros.mostrar(todos)
busqueda = st.text_input("Buscar por denominación", key="ficha_busqueda")
filtrados = filtros.buscar_denominacion(filtrados, busqueda)
codigos = filtrados["codigo_cnv"].tolist()
if st.session_state.get("ficha_fondo") not in codigos:
    st.session_state["ficha_fondo"] = None
if filtrados.empty:
    st.info("No hay fondos para estos filtros y búsqueda.")
    st.stop()
nombres = filtrados.set_index("codigo_cnv")["nombre"]
codigo = st.selectbox("Fondo", codigos, index=None, key="ficha_fondo",
                      placeholder="Elegí un fondo",
                      format_func=lambda c: f"{nombres.loc[c]} · CNV {c}")
if codigo is None:
    st.info("Elegí un fondo para ver su ficha.")
    st.stop()
fondo = filtrados[filtrados["codigo_cnv"] == codigo].iloc[0]
nombre = fondo["nombre"]
codigo = int(fondo["codigo_cnv"])
seguido = bool(fondo["seguido"])
puntual = None
if not seguido:
    cache = st.session_state.setdefault("consulta_puntual", {})
    puntual = cache.get("fondos", {}).get(codigo)
    if st.button("Ver ahora sin seguir", disabled=puntual is not None):
        with st.spinner("Cargando…"):
            puntual = consulta_puntual.cargar(fondo.to_dict(), cache,
                                             consulta_puntual.token_configurado(st.secrets))
    if puntual is not None:
        st.info(consulta_puntual.LEYENDA)
        st.caption(f"Carga inicial: {formato.numero(puntual['segundos'])} segundos · disponible solo en esta sesión.")
        for aviso in puntual["avisos"]:
            st.warning(aviso)
        fondo = fondo.copy()
        for campo, valor in puntual["cabecera"].items():
            fondo[campo] = valor
        fondo["consulta_puntual"] = True
    if st.button("Seguir este fondo"):
        st.session_state["seguir_desde_ficha"] = codigo
        st.switch_page("pages/7_Sumar_o_quitar_fondos.py")
tabla_pdf = pd.DataFrame()
serie_pdf = pd.DataFrame()
rubros_pdf = pd.DataFrame()
top_pdf = pd.DataFrame()
costos_pdf = pd.DataFrame()
notas_pdf = []
if puntual is not None:
    notas_pdf = [consulta_puntual.LEYENDA, *puntual["avisos"]]

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
        if seguido:
            rend = datos.rendimientos_ultimos(sel["codigo_cafci"])
            tabla = sel.merge(rend, on="codigo_cafci", how="left")
        elif puntual is not None and not puntual["rendimientos"].empty:
            tabla = sel.merge(puntual["rendimientos"], on="codigo_cafci", how="left")
        else:
            tabla = datos.foto_de_fondo(codigo)
            tabla = tabla[tabla["codigo_cafci"].isin(sel["codigo_cafci"])].copy()
            for campo in ("diario", "dias_7", "dias_30"):
                tabla[campo] = None
            tabla["revisar"] = False
        revisar = tabla[tabla["revisar"] == True]["nombre"].tolist()  # noqa: E712
        tabla = tabla.rename(columns={"nombre": "Clase", "moneda": "Moneda", "fecha": "Fecha",
                                      "cuotaparte": "Cuotaparte", "patrimonio": "Patrimonio",
                                      "diario": "Diario %", "dias_7": "7 días %", "dias_30": "30 días %",
                                      "mes": "Mes %", "anio": "Año %", "meses_12": "12 meses %"})
        cols = ["Clase", "Moneda", "Fecha", "Cuotaparte", "Patrimonio", "Diario %", "7 días %",
                "30 días %", "Mes %", "Año %", "12 meses %"]
        tabla_pdf = formato.tabla(tabla[cols], fechas=["Fecha"], enteros=["Patrimonio"],
                                   numeros={"Cuotaparte": 4, **{c: 2 for c in cols if c.endswith("%")}})
        st.dataframe(tabla_pdf,
                     hide_index=True, width="stretch")
        calculados = seguido or (puntual is not None and not puntual["rendimientos"].empty)
        nota = ('Rendimientos calculados con las reglas del robot sobre la cuotaparte; "—" = historia insuficiente '
                '(o clase sin patrimonio) para ese período. No consideran distribución de utilidades.' if calculados else
                'Rendimientos de la CAFCI: mes, año y 12 meses. "—" = dato no disponible. '
                'No consideran distribución de utilidades.')
        st.caption(nota)
        notas_pdf.append(nota)
        if not seguido:
            costos_pdf = calculos.costos_ficha(datos.foto_de_fondo(codigo))
            st.dataframe(costos_pdf, hide_index=True, width="stretch")
        if revisar and seguido:
            st.warning("Diario, mes, año o 12 meses calculados distintos de los de la CAFCI en: " + ", ".join(revisar)
                       + ". Ver la pantalla \"Filas a revisar\".")

        if seguido or puntual is not None:
            historia = datos.serie(sel["codigo_cafci"]) if seguido else puntual["serie"]
            ser = historia.merge(sel[["codigo_cafci", "nombre"]], on="codigo_cafci")
            ser = calculos.base_100(ser, ser["fecha"].min())
            serie_pdf = ser
            if calculos.historia_insuficiente(ser):
                st.info("Historia insuficiente en alguna clase: se necesitan dos cuotapartes válidas para mostrar evolución.")
            if ser["base 100"].notna().any():
                fig = px.line(ser, x="fecha", y="base 100", color="nombre", markers=True,
                              title="Evolución de la cuotaparte (base 100 en la primera fecha válida)")
                fig.update_traces(connectgaps=False)
                fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
                st.plotly_chart(formato.plotly_es(fig, fechas_x=True), width="stretch")

# ───────── Cartera ─────────
st.subheader("Cartera semanal")
enc = datos.cartera_encabezado(codigo) if seguido else pd.DataFrame()
cart = datos.cartera(codigo) if seguido else pd.DataFrame()
if puntual is not None:
    enc, cart = puntual["encabezado"], puntual["cartera"]
if not seguido and puntual is None:
    st.info(filtros.AVISO_NO_SEGUIDO)
elif enc.empty or cart.empty:
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
    rubros_pdf = resumen
    notas_pdf.append(f"Cartera al {formato.fecha(e['fecha_cartera'])} · patrimonio {formato.entero(e['patrimonio'])} {e['moneda']}")
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
    notas_pdf.append(f"Pasivos (implícitos): {formato.numero(pasivos)}% del patrimonio.")
    dif = 100 - float(cart["pct_pn"].sum())
    if abs(dif) > 0.05:
        st.caption(f"Las líneas suman {formato.numero(100 - dif)}%: la diferencia de {formato.numero(dif)} puntos es "
                   "redondeo de la CNV (cada renglón viene redondeado a 2 decimales).")
    filtrada = calculos.filtrar_por_rubros(activos, elegidos)
    top = filtrada.sort_values("pct_pn", ascending=False).head(10)
    st.markdown("**10 mayores tenencias**" + (f" de {', '.join(elegidos)}" if elegidos else ""))
    top_pdf = formato.tabla(top[["instrumento", "pct_pn", "categoria_detallada"]].rename(
        columns={"instrumento": "Instrumento", "pct_pn": "% del PN", "categoria_detallada": "Categoría"}),
        numeros={"% del PN": 2})
    st.dataframe(top_pdf, hide_index=True, width="stretch")
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

# El PDF recibe los mismos datos seleccionados y formateados que la pantalla.
st.download_button("Descargar PDF", data=informe.pdf_ficha(
    fondo.to_dict(), tabla_pdf, serie_pdf, rubros_pdf, top_pdf, notas_pdf,
    costos_pdf),
    file_name=f"FCI_Ficha_{codigo}.pdf", mime="application/pdf")
