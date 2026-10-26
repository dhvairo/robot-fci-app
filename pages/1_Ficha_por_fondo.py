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
import marca  # noqa: E402
import informe  # noqa: E402
import consulta_puntual  # noqa: E402
import rapidez  # noqa: E402
import pandas as pd
from copy import deepcopy

def memorizar(clave, calcular, *seleccion):
    """Lo derivado de datos propios de la sesión (consulta puntual) no se memoriza."""
    if puntual is not None:
        return calcular()
    return rapidez.memorizar(clave, calcular, *seleccion)


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
        acceso.requerir_admin_clasificacion(escritura=False)
    except acceso.ErrorAcceso:
        motivo = 'función administrador'
        st.caption(motivo)
    if not vista or vista['estado'] != 'habilitado':
        motivo = (vista or {}).get('mensaje', 'La clasificación por organización no está disponible.')
    filas = [f for f in (vista or {}).get('filas', []) if f['codigo_cnv'] == codigo]
    por_orden = {f['orden']: f for f in filas}
    # El índice de tabla solo presenta; la elección guarda el UUID y snapshot completos.
    def preparar_tabla_edicion():
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
        return tabla
    tabla = memorizar('ficha_tabla_edicion', preparar_tabla_edicion, codigo, str(fecha),
        tuple(cart.orden), motivo,
        repr([(f['orden'], f.get('procedencia'), f.get('pendiente'), f.get('bloqueo')) for f in filas])).copy()
    tabla.insert(0, 'Editar', False)
    clave = f"ficha_edicion_{persona.get('id')}_{persona.get('organizacion')}_{codigo}_{fecha}"
    if clave + '_lote_resultado' in st.session_state:
        st.success(st.session_state.pop(clave + '_lote_resultado'))
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
    todos_marcados = st.checkbox('Todos', key=tabla_clave + '_todos', disabled=bool(motivo))
    tabla['Editar'] = todos_marcados
    tabla_clave += str(todos_marcados)
    editada = st.data_editor(tabla, hide_index=True, width='stretch',
                            disabled=True if motivo else [c for c in tabla if c != 'Editar'],
                            key=tabla_clave)
    elegidas = [por_orden.get(o) for o, elegido in zip(cart.orden, editada['Editar']) if elegido]
    if elegidas:
        seleccion = tuple(f['identidad']['instrumento_id'] for f in elegidas if f)
        lote_clave = clave + '_lote'
        if st.session_state.get(lote_clave + '_seleccion') != seleccion:
            st.session_state[lote_clave + '_seleccion'] = seleccion
            st.session_state[lote_clave] = deepcopy(elegidas)
        originales = st.session_state[lote_clave]
        st.markdown('**Editar las filas marcadas**')
        campo = st.selectbox('Campo a editar', list(ETIQUETAS), format_func=ETIQUETAS.get, key=lote_clave + '_campo')
        opciones = (vista or {}).get('opciones', ())
        indice = list(calculos.CAMPOS_CLASIFICACION).index(campo)
        valores = sorted({o[indice] for o in opciones}, key=lambda x: str(x or ''))
        nuevo = st.selectbox('Valor nuevo', valores, format_func=lambda x: x or 'Sin dato', key=lote_clave + '_valor_' + campo)
        cambios = {campo: nuevo}
        error = ''
        cantidad = 0
        try:
            cantidad = sum(calculos.parche_edicion_ficha(f, cambios, opciones) is not None for f in originales)
        except (calculos.ErrorClasificacion, TypeError):
            error = 'El valor no forma una clasificación válida en todas las filas marcadas.'
            st.warning(error)
        st.caption(f'{cantidad} de {len(originales)} filas cambian.')
        distintos = len({f['efectiva'].get('categoria_resumen') for f in originales if f}) > 1
        confirmado = True
        if distintos:
            st.warning('Las filas marcadas pertenecen a rubros distintos.')
            confirmado = st.checkbox('Confirmo editar filas de rubros distintos',
                key=lote_clave + '_confirmar_' + str((seleccion, campo, nuevo)))
        if st.button('Confirmar edición masiva', key=lote_clave + '_guardar',
                     disabled=bool(motivo or error) or not cantidad or not confirmado):
            try:
                resultado = datos.guardar_edicion_ficha_lote(originales, cambios, opciones)
            except (acceso.ErrorAcceso, calculos.ErrorClasificacion):
                st.error('No se guardó ninguna fila del lote; recargá y revisá las filas.')
                return
            if resultado['estado'] == 'guardado':
                st.session_state.pop(lote_clave + '_seleccion', None)
                st.session_state.pop(tabla_clave, None)
                st.session_state.pop(tabla_clave[:-len(str(todos_marcados))] + '_todos', None)
                st.session_state[clave + '_lote_resultado'] = f"Se guardaron {resultado['cantidad']} filas."
                st.rerun()
            else:
                st.warning(resultado.get('mensaje', 'No se guardó ninguna fila del lote.'))
        if len(elegidas) > 1:
            return
    fila = elegidas[0] if elegidas else None
    if fila is None:
        st.session_state.pop(clave, None)
        st.session_state.pop(clave + '_lote_seleccion', None)
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
PALETA = marca.COLORES_RUBROS
GRIS = marca.RUBRO_GRIS

def mostrar_tabla(tabla, **opciones):
    if st.session_state.get("ficha_vista", "Tabla") == "Tarjetas" and "on_select" not in opciones:
        base = tabla.data if hasattr(tabla, "data") else tabla
        columnas = tuple(str(c) for c in base.columns)
        filas = tuple(tuple(formato.texto_o_guion(v) if not pd.isna(v) else formato.VACIO
                            for v in fila) for fila in base.itertuples(index=False, name=None))
        html = rapidez.memorizar("ficha_tarjetas", lambda: formato.tarjetas_html(columnas, filas), columnas, filas)
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.dataframe(tabla, **opciones)


def leer_complemento(codigo):
    try:
        resultado = datos.ficha_unica(codigo)
        return resultado.get('ficha')
    except datos.TopeLectura:
        st.warning('Se alcanzó el tope diario de lectura. Volvé a intentar mañana.')
    except datos.psycopg.Error:
        st.warning('No se pudo consultar la Ficha Única. Volvé a intentar más tarde.')
    return None


def mostrar_tematica_sugerida(codigo, fondo, ficha, complemento):
    if not ficha:
        return
    objetivo = next((d for d in ficha['datos'] if d['campo'] == 'objetivo_politica'), {})
    sugerencia = calculos.texto_complemento(objetivo)
    if complemento['sugerencia']:
        st.markdown(f"**Temática sugerida por la ficha:** {complemento['sugerencia']}")
    if not sugerencia:
        return
    try:
        formulario = datos.formulario_tematica_ficha(codigo, fondo)
        if formulario is not None and st.button('Usar como temática', key=f'usar_tematica_ficha_{codigo}'):
            datos.anotar_pedido(codigo, 'editar_tematica', idea=sugerencia, formulario=formulario)
            st.success('Pedido de temática anotado. Se aplicará en la próxima corrida del robot.')
    except (acceso.ErrorAcceso, calculos.ErrorSeguimiento, datos.ErrorPedido):
        st.caption('La temática se conserva. Podés revisarla en Sumar o quitar fondos.')



st.set_page_config(page_title="Ficha por fondo", page_icon="📈", layout="wide")
acceso.requerir()
inicial_seguimiento = calculos.filtro_seguimiento_inicial(st.session_state)
st.title("Ficha por fondo")
st.segmented_control("Vista", ["Tabla", "Tarjetas"], default="Tabla", key="ficha_vista")

st.button("Restablecer", on_click=filtros.restablecer_ficha, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True, progresivo=True)
vista = todos.attrs.get('clasificacion')
pendiente = todos.attrs.get('pendiente', False)
if pendiente:
    inicial_seguimiento = 'Todos'
    tareas = (st.session_state['_arranque_privado'][3], st.session_state['_arranque_ficha'])
    seguimiento = st.session_state.get('_arranque_seguimiento')
    rapidez.esperar_sin_consultas((*tareas, seguimiento[2] if seguimiento else None))
if todos.empty:
    st.session_state["ficha_fondo"] = None
    st.info("No hay fondos en el catálogo.")
    st.stop()
filtrados = filtros.mostrar(todos, inicial=inicial_seguimiento)
filtrados = filtrados.sort_values("nombre", key=lambda s: s.str.casefold())
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
st.subheader(nombre)
if st.session_state.get('_arranque_privado'):
    detalle = rapidez.preparar_fondo(codigo)
    if pendiente or not detalle.done():
        st.caption(f"Código CNV {codigo} · {fondo['estado']}")
        st.subheader("Clases")
        mostrar_tabla(formato.tabla(datos.clases_de_fondo(codigo), enteros=['codigo_cafci']), hide_index=True, width='stretch')
        rapidez.esperar_sin_consultas((detalle,))
        st.stop()
    rapidez._esperar(detalle)
if not seguido and not pendiente:
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
clases_sel, rubros_sel = (), ()
if puntual is not None:
    notas_pdf = [consulta_puntual.LEYENDA, *puntual["avisos"]]

ficha = leer_complemento(codigo)
complemento = calculos.integrar_ficha_unica(fondo.to_dict(), ficha)
cab = complemento['cabecera']
for i in range(0, len(cab), 3):
    for col, (etiqueta, valor) in zip(st.columns(3), cab[i:i + 3]):
        col.markdown(f"**{etiqueta}**  \n{valor}")
descripcion, idea = calculos.tematica(fondo.to_dict())
st.markdown(f"**Descripción breve:** {complemento['descripcion']}")
st.markdown(f"**Idea y temática:** {idea}")
if not pendiente:
    mostrar_tematica_sugerida(codigo, fondo, ficha, complemento)
for etiqueta, valor in complemento['extras']:
    st.markdown(f"**{etiqueta}:** {valor}")
for aviso in complemento['avisos']:
    st.caption(aviso)
if not complemento['costos'].empty:
    mostrar_tabla(formato.tabla(complemento['costos']), hide_index=True, width='stretch')
if ficha:
    fuente = {'gerente': 'web del gerente', 'cafci': 'CAFCI'}.get(ficha['fuente'], '')
    documento = ficha.get('url') or ''
    enlace = f" · [Ver documento](<{documento}>)" if documento.startswith(('https://', 'http://')) else ''
    st.caption(f"Datos complementados con la Ficha Única al {formato.fecha(ficha['fecha_referencia'])} · fuente: {fuente}{enlace}")
aviso_rendimiento_ficha = st.empty()
st.caption(f"Código CNV {codigo} · {fondo['estado']}")

# ───────── Clases y rendimientos ─────────
with datos.seccion_disponible():
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
        clases_sel = tuple(elegidas)
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
            mostrar_tabla(tabla_pdf,
                         hide_index=True, width="stretch")
            calculados = seguido or (puntual is not None and not puntual["rendimientos"].empty)
            nota = ('Rendimientos calculados con las reglas del robot sobre la cuotaparte; "—" = historia insuficiente '
                    '(o clase sin patrimonio) para ese período. No consideran distribución de utilidades.' if calculados else
                    'Rendimientos de la CAFCI: mes, año y 12 meses. "—" = dato no disponible. '
                    'No consideran distribución de utilidades.')
            st.caption(nota)
            notas_pdf.append(nota)
            if not seguido and ficha is None:
                costos_pdf = calculos.costos_ficha(datos.foto_de_fondo(codigo))
                mostrar_tabla(costos_pdf, hide_index=True, width="stretch")
            if revisar and seguido:
                st.warning("Diario, mes, año o 12 meses calculados distintos de los de la CAFCI en: " + ", ".join(revisar)
                           + ". Ver la pantalla \"Filas a revisar\".")

            if seguido or puntual is not None:
                historia = datos.serie(sel["codigo_cafci"]) if seguido else puntual["serie"]
                if calculos.control_rendimientos_ficha(ficha, historia, sel):
                    aviso_rendimiento_ficha.caption(
                        f"Rendimiento de la Ficha Única no coincide con el cálculo al {formato.fecha(ficha['fecha_referencia'])[:5]}")
                def armar_serie():
                    ser = historia.merge(sel[["codigo_cafci", "nombre"]], on="codigo_cafci")
                    ser = calculos.base_100(ser, ser["fecha"].min())
                    fig = None
                    if ser["base 100"].notna().any():
                        fig = px.line(ser, x="fecha", y="base 100", color="nombre", markers=True,
                                      title="Evolución de la cuotaparte (base 100 en la primera fecha válida)")
                        fig.update_traces(connectgaps=False)
                        fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
                        fig = formato.plotly_es(fig, fechas_x=True)
                    return ser, fig
                ser, fig = memorizar("ficha_serie", armar_serie, codigo, clases_sel)
                serie_pdf = ser
                if calculos.historia_insuficiente(ser):
                    st.info("Historia insuficiente en alguna clase: se necesitan dos cuotapartes válidas para mostrar evolución.")
                if fig is not None:
                    st.plotly_chart(fig, width="stretch")

    # ───────── Cartera ─────────
with datos.seccion_disponible():
    st.subheader("Cartera semanal")
    enc = datos.cartera_encabezado(codigo, vista) if seguido else pd.DataFrame()
    cart = datos.cartera(codigo, vista) if seguido else pd.DataFrame()
    if puntual is not None:
        enc, cart = puntual["encabezado"], puntual["cartera"]
    if pendiente:
        st.caption('Cargando…')
    elif not seguido and puntual is None:
        st.info(filtros.AVISO_NO_SEGUIDO)
    elif enc.empty or cart.empty:
        st.info("Todavía no hay cartera cargada para este fondo.")
    else:
        e = enc.iloc[0]
        parcial = calculos.leyenda_cartera(e.get('fuente', 'cnv'))
        if parcial:
            st.caption(parcial)
            notas_pdf.append(parcial)
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
            rubros_sel = tuple(elegidos)
            colores = [m[1] if (not elegidos or r in elegidos) else m[1] + "40"
                       for m, r in zip(marcas, resumen["categoria_resumen"])]
            def armar_torta():
                fig = go.Figure(go.Pie(labels=resumen["categoria_resumen"], values=resumen["pct_pn"], hole=0.4, sort=False,
                                       marker=dict(colors=colores),
                                       pull=[0.08 if r in elegidos else 0 for r in resumen["categoria_resumen"]]))
                fig.update_layout(title="Por rubro", margin=dict(t=50, b=10))
                return formato.plotly_es(fig)
            st.plotly_chart(memorizar("ficha_torta", armar_torta, codigo, rubros_sel), width="stretch")
        det = activos.groupby("categoria_detallada", as_index=False)["pct_pn"].sum()
        det = det[det["pct_pn"] > 0].sort_values("pct_pn")
        g2.plotly_chart(memorizar("ficha_barras", lambda: formato.plotly_es(px.bar(
            det, x="pct_pn", y="categoria_detallada", orientation="h", title="Por rubro, moneda e indexación (% del PN)",
            labels={"pct_pn": "% del PN", "categoria_detallada": ""})), codigo), width="stretch")
        st.pills("Rubros (tocá uno o más para ver solo sus instrumentos; tocá de nuevo para sacarlo)",
                 list(etiquetas), format_func=etiquetas.get, selection_mode="multi", key=f"rubros_{codigo}")
        if not parcial:
            st.caption(f"Pasivos (implícitos): {formato.numero(pasivos)}% del patrimonio. Se muestran aparte porque restan.")
            notas_pdf.append(f"Pasivos (implícitos): {formato.numero(pasivos)}% del patrimonio.")
        dif = 100 - float(cart["pct_pn"].sum())
        if abs(dif) > 0.05:
            st.caption(f"Las líneas suman {formato.numero(100 - dif)}%: la diferencia de {formato.numero(dif)} puntos es "
                       "redondeo de la fuente.")
        filtrada = calculos.filtrar_por_rubros(activos, elegidos)
        top = filtrada[filtrada["categoria_resumen"] != "Sin detalle"].sort_values("pct_pn", ascending=False).head(10)
        st.markdown("**10 mayores tenencias**" + (f" de {', '.join(elegidos)}" if elegidos else ""))
        top_pdf = formato.tabla(top[["instrumento", "pct_pn", "categoria_detallada"]].rename(
            columns={"instrumento": "Instrumento", "pct_pn": "% del PN", "categoria_detallada": "Categoría"}),
            numeros={"% del PN": 2})
        mostrar_tabla(top_pdf, hide_index=True, width="stretch")
        with st.expander("Cartera completa"):
            mostrar_edicion_clasificacion(
                calculos.filtrar_por_rubros(cart, elegidos),
                puntual.get('clasificacion') if puntual is not None else vista, codigo, e['fecha_cartera'])

if st.button("Ver cambios de cartera"):
    st.session_state["cambios_fondo"] = codigo
    st.session_state["cambios_origen"] = codigo
    st.session_state["ficha_fondo"] = codigo
    st.switch_page("pages/11_Cambios_de_cartera.py")

# ───────── Hechos relevantes ─────────
with datos.seccion_disponible():
    st.subheader("Hechos relevantes")
    h = datos.hechos(codigo)
    if h.empty:
        st.info("Sin hechos relevantes registrados.")
    else:
        tipos = sorted(t for t in h["tipo"].unique() if t)
        filtro = st.multiselect("Filtrar por tipo", tipos)
        if filtro:
            h = h[h["tipo"].isin(filtro)]
        mostrar_tabla(formato.tabla(h.rename(columns={"fecha": "Fecha", "tipo": "Tipo", "descripcion": "Descripción",
                                                     "link": "Documento"}), fechas=["Fecha"]),
                     hide_index=True, width="stretch",
                     column_config={"Documento": st.column_config.LinkColumn("Documento", display_text="Abrir")})

# El PDF recibe los mismos datos seleccionados y formateados que la pantalla.
if not pendiente:
    rapidez.pdf_a_pedido(
    "ficha", (codigo, clases_sel, rubros_sel, puntual is not None),
    lambda: informe.pdf_ficha(fondo.to_dict(), tabla_pdf, serie_pdf, rubros_pdf, top_pdf, notas_pdf, costos_pdf),
    f"FCI_Ficha_{codigo}.pdf", "Descargar PDF", memorizado=puntual is None)
