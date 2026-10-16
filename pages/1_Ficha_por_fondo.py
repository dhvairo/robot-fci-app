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
from copy import deepcopy

ETIQUETAS = dict(zip(calculos.CAMPOS_CLASIFICACION, ('Rubro', 'Categoría', 'Moneda', 'Indexación')))


def mostrar_novedades_clasificacion(filas, clave, motivo):
    pendientes = {f['identidad']['instrumento_id']: f for f in filas
                  if f.get('pendiente') and not f.get('bloqueo')}
    if not pendientes:
        st.session_state.pop(clave + '_snapshot', None)
        return
    st.warning('La fuente tiene una nueva clasificación. Podés usarla o conservar la de tu organización.')
    elegido = st.selectbox('Novedad de clasificación', list(pendientes), index=None,
        format_func=lambda i: f"{pendientes[i]['instrumento']} · {i}", key=clave + '_elegida')
    if elegido is None:
        st.session_state.pop(clave + '_snapshot', None)
        return
    anterior = st.session_state.get(clave + '_snapshot')
    if anterior is None or anterior['identidad'] != pendientes[elegido]['identidad']:
        st.session_state[clave + '_snapshot'] = deepcopy(pendientes[elegido])
    original = st.session_state[clave + '_snapshot']
    st.write('Clasificación original de la fuente:',
             {ETIQUETAS[k]: original.get(k) for k in calculos.CAMPOS_CLASIFICACION})
    st.write('Clasificación efectiva de tu organización:',
             {ETIQUETAS[k]: original['efectiva'].get(k) for k in calculos.CAMPOS_CLASIFICACION})
    st.caption('Usar la fuente desactiva todo el parche y sigue sus cambios futuros. '
               'Conservar mantiene solo los campos administrativos; los heredados siguen a la fuente.')
    if st.button('Actualizar novedad', key=clave + '_actualizar'):
        st.session_state.pop(clave + '_snapshot', None)
        st.rerun()
    for accion, etiqueta in (('aceptar', 'Usar clasificación de la fuente'),
                             ('conservar', 'Conservar clasificación de mi organización')):
        if st.button(etiqueta, disabled=bool(motivo), key=clave + '_' + accion):
            try:
                resultado = datos.resolver_novedad_ficha(original, accion)
            except (acceso.ErrorAcceso, calculos.ErrorClasificacion) as error:
                st.error(str(error))
                return
            if resultado['estado'] != 'guardado':
                st.warning(resultado.get('mensaje', 'No se resolvió la novedad.'))
                return
            st.session_state.pop(clave + '_snapshot', None)
            st.session_state[clave + '_resultado'] = (
                'Clasificación de la fuente aceptada' if accion == 'aceptar'
                else 'Se conservó la clasificación de tu organización')
            st.rerun()


def mostrar_edicion_clasificacion(cart, vista, codigo, fecha):
    persona = acceso.usuario() or {}
    motivo = ''
    try:
        acceso.requerir_admin_clasificacion()
    except acceso.ErrorAcceso:
        motivo = 'función administrador'
        st.caption(motivo)
    if not vista or vista['estado'] != 'habilitado':
        motivo = (vista or {}).get('mensaje', 'La clasificación por organización no está disponible.')
    filas = [f for f in (vista or {}).get('filas', []) if f['codigo_cnv'] == codigo]
    por_orden = {f['orden']: f for f in filas}
    # El índice de tabla solo presenta; la elección guarda el UUID y snapshot completos.
    tabla = cart.drop(columns=['orden']).copy()
    tabla = tabla[[c for c in ('rubro_cnv', 'instrumento', 'pct_pn',
                              *calculos.CAMPOS_CLASIFICACION, 'sin_clasificar') if c in tabla]]
    tabla = tabla.rename(columns={'rubro_cnv': 'Rubro CNV', 'instrumento': 'Instrumento',
                                  'pct_pn': '% del PN', 'sin_clasificar': 'Sin clasificar', **ETIQUETAS})
    tabla['Fecha'] = formato.fecha(fecha)
    tabla['Procedencia'] = [por_orden.get(o, {}).get('procedencia', 'fuente') for o in cart.orden]
    tabla['Novedad de fuente'] = ['Pendiente' if por_orden.get(o, {}).get('pendiente') else ''
                                for o in cart.orden]
    tabla['Motivo'] = [motivo or por_orden.get(o, {}).get('bloqueo') or '' for o in cart.orden]
    for campo in calculos.CAMPOS_CLASIFICACION:
        tabla[f'{ETIQUETAS[campo]} original'] = [por_orden.get(o, {}).get(campo) for o in cart.orden]
    tabla = formato.tabla(tabla, numeros={'% del PN': 2}, booleanos=['Sin clasificar'])
    tabla.insert(0, 'Editar', False)
    clave = f"ficha_edicion_{persona.get('id')}_{persona.get('organizacion')}_{codigo}_{fecha}"
    mostrar_novedades_clasificacion(filas, clave + '_novedad', motivo)
    if clave + '_novedad_resultado' in st.session_state:
        st.success(st.session_state.pop(clave + '_novedad_resultado'))
    tabla_clave = clave + '_tabla' + str(tuple(
        (o, por_orden.get(o, {}).get('identidad', {}).get('instrumento_id')) for o in cart.orden))
    st.caption('ítems clasificados por administrador · procedencia organización; los demás conservan la fuente.')
    st.caption('La fuente común combina datos de CNV y reglas comunes. '
               'La observación inicial no reconstruye clasificaciones históricas.')
    if motivo:
        st.caption(motivo)
    editada = st.data_editor(tabla, hide_index=True, width='stretch',
                            disabled=True if motivo else [c for c in tabla if c != 'Editar'],
                            key=tabla_clave)
    elegidas = [por_orden.get(o) for o, elegido in zip(cart.orden, editada['Editar']) if elegido]
    if len(elegidas) > 1:
        st.warning('Elegí una sola fila para editar.')
        return
    fila = elegidas[0] if elegidas else None
    if fila is None:
        st.session_state.pop(clave, None)
        return
    if fila['bloqueo']:
        motivo = fila['bloqueo']
        st.caption(motivo)
    # No renovar versiones durante la edición: una escritura concurrente debe dar conflicto.
    anterior = st.session_state.get(clave)
    if anterior is None or anterior['identidad'] != fila['identidad']:
        st.session_state[clave] = deepcopy(fila)
    original = st.session_state[clave]
    opciones = (vista or {}).get('opciones', ())
    prefijo, valores = (), {}
    for i, campo in enumerate(calculos.CAMPOS_CLASIFICACION):
        disponibles = list(dict.fromkeys(t[i] for t in opciones if t[:i] == prefijo))
        actual = original['efectiva'].get(campo)
        valor = st.selectbox(ETIQUETAS[campo], disponibles,
                             index=disponibles.index(actual) if actual in disponibles else 0 if disponibles else None,
                             format_func=lambda x: 'Sin dato' if x is None else x,
                             disabled=bool(motivo) or not disponibles,
                             key=clave + str(original['identidad']['instrumento_id']) + str(i) + str(prefijo))
        valores[campo] = valor
        prefijo += (valor,)
    if st.button('Cancelar', key=clave + '_cancelar'):
        st.session_state.pop(clave, None)
        st.session_state.pop(tabla_clave, None)
        st.rerun()
    if st.button('Guardar', disabled=bool(motivo) or not opciones, key=clave + '_guardar'):
        try:
            resultado = datos.guardar_edicion_ficha(original, valores, opciones)
        except (acceso.ErrorAcceso, calculos.ErrorClasificacion) as error:
            st.error(str(error))
            return
        if resultado['estado'] in ('guardado', 'sin_cambios'):
            st.session_state.pop(clave, None)
            st.session_state.pop(tabla_clave, None)
            st.rerun()  # Recompone fuente cacheada y privadas con Auth de esta sesión.
        else:
            st.warning(resultado.get('mensaje', 'No se guardó la clasificación.'))


# Colores de la torta; cada uno tiene su cuadradito de color para el botón del mismo rubro.
PALETA = [("🔴", "#E04B4B"), ("🟠", "#F28E2B"), ("🟡", "#EDC948"), ("🟢", "#59A14F"), ("🔵", "#4E79A7"),
          ("🟣", "#B07AA1"), ("🟤", "#9C755F"), ("⚫", "#444444")]
GRIS = ("⚪", "#BAB0AC")

st.set_page_config(page_title="Ficha por fondo", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Ficha por fondo")

st.button("Restablecer", on_click=filtros.restablecer_ficha, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
vista = todos.attrs.get('clasificacion')
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
        puntual = consulta_puntual.efectiva(puntual, fondo.to_dict())
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
enc = datos.cartera_encabezado(codigo, vista) if seguido else pd.DataFrame()
cart = datos.cartera(codigo, vista) if seguido else pd.DataFrame()
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
        mostrar_edicion_clasificacion(
            calculos.filtrar_por_rubros(cart, elegidos),
            puntual.get('clasificacion') if puntual is not None else vista, codigo, e['fecha_cartera'])

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
