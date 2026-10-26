"""Comparador: filtros comunes, selección múltiple, R/P y PDF."""
from datetime import timedelta

import pandas as pd
import streamlit as st

import recarga
recarga.al_dia()

import acceso
import calculos
import consulta_puntual
import datos
import filtros
import formato
import informe
import marca
import rapidez

st.set_page_config(page_title="Comparador", page_icon="📈", layout="wide")
acceso.requerir()
inicial_seguimiento = calculos.filtro_seguimiento_inicial(st.session_state)
st.title("Comparador")
if not st.session_state.get('comparador_llevar', st.session_state.get('comparador_llevar_memoria', False)):
    st.session_state.pop(calculos.SELECCION_INFORME, None)

if not st.session_state.get('comparador_llevar_gestion', st.session_state.get('comparador_llevar_gestion_memoria', False)):
    st.session_state.pop(calculos.SELECCION_GESTION, None)

st.button("Restablecer", on_click=filtros.restablecer_comparador, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
if todos.empty:
    st.info("No hay fondos en el catálogo.")
    st.stop()
catalogo_clases = rapidez.leer_comun('COMPARADOR_CATALOGO_CLASES',
    lambda: calculos.clases_comparador(todos[['codigo_cnv', 'nombre']], [], datos.clases_de_fondo))
nombres_clases = sorted(calculos.agrupar_clases_comparador(catalogo_clases).unique(),
                        key=lambda n: (n == 'Sin clase', n))
for llave in list(st.session_state):
    if llave.startswith('comparador_clases_casilla_') or llave == 'comparador_clases_todas':
        st.session_state.pop(llave)
memoria_clases = st.session_state.get('comparador_clases_memoria', [])
if isinstance(memoria_clases, dict):
    st.session_state['comparador_clases_memoria'] = [n for n in nombres_clases if memoria_clases.get(n)]
ver_todas = st.checkbox('Ver todas las clases', value=False)
grupos_preferidos = [datos.clases_preferidas(g) for _, g in catalogo_clases.groupby('codigo_cnv')]
preferidas = pd.concat(grupos_preferidos, ignore_index=True) if grupos_preferidos else catalogo_clases
clases_defecto = sorted(calculos.agrupar_clases_comparador(preferidas).unique())
if 'comparador_clases_memoria' not in st.session_state:
    st.session_state['comparador_clases_memoria'] = clases_defecto
if not ver_todas and any(g.attrs.get('sin_activas') for g in grupos_preferidos):
    st.caption('Sin clases con movimiento comprobado en 30 días: se muestran todas.')
elegidas = filtros.lista_fondos('Clase', {n: n for n in nombres_clases}, 'comparador_clases',
                               vacio_todos=False, conservar_orden=True)
base = filtros.mostrar(todos, clave="comparador", persistente=True, inicial=inicial_seguimiento, multiple=True)
monedas = list(st.session_state['comparador_filtro_1'])
clases = calculos.filtrar_moneda(catalogo_clases, monedas)
if not ver_todas and elegidas == clases_defecto:
    clases = clases[clases.codigo_cafci.isin(preferidas.codigo_cafci)]
base = calculos.proponer_fondos_comparador(base, clases, elegidas)
if elegidas and not (ver_todas and elegidas == clases_defecto):
    clases = clases[calculos.agrupar_clases_comparador(clases).isin(elegidas)]
nombres = base.set_index("codigo_cnv")["nombre"].to_dict()
with st.expander("Fondos", expanded=False):
    elegidos = filtros.lista_fondos("Fondos", nombres, "comparador_fondos", vacio_todos=False,
                                   mostrar_etiquetas=False)
    fondos = base[base.codigo_cnv.isin(elegidos)]
    clases = clases[clases.codigo_cnv.isin(elegidos)]
    controles_catalogo = calculos.controles_fondos_comparador(st.session_state, clases, elegidos) if elegidos else []
    with st.container(horizontal=True, key='comparador_botones', vertical_alignment='center'):
        for fondo in fondos.itertuples():
            codigos = set(clases.loc[clases.codigo_cnv.eq(fondo.codigo_cnv), 'codigo_cafci'])
            control = next((c for c in controles_catalogo if c['codigo'] in codigos), None)
            with st.container(horizontal=True, width='content', gap='small'):
                llave_serie = str(control['codigo']) if control else f'fondo_{fondo.codigo_cnv}'
                st.button(fondo.nombre if len(fondo.nombre) <= 22 else fondo.nombre[:21] + '…',
                          key=f"comparador_serie_{llave_serie}", width='content',
                          help=fondo.nombre + ' · Apagar o encender este fondo'
                               + ('' if control else ' · sin clases disponibles'),
                          on_click=calculos.alternar_fondo_comparador,
                          args=(st.session_state, int(fondo.codigo_cnv)))
                st.button('×', key=f'comparador_quitar_{fondo.codigo_cnv}', width='content',
                          help=f'Quitar {fondo.nombre}', on_click=calculos.quitar_fondo_comparador,
                          args=(st.session_state, int(fondo.codigo_cnv)))
if not elegidos or clases.empty:
    st.session_state.pop(calculos.SELECCION_INFORME, None)
    st.session_state.pop(calculos.SELECCION_GESTION, None)
if not elegidos:
    st.info("Elegí al menos un fondo.")
    st.stop()
if clases.empty:
    st.info("No hay clases disponibles para estos fondos y moneda.")
    st.stop()
sel = clases
if len(sel) > 30:
    st.warning("Se muestran las primeras 30 clases como máximo. Afiná la selección para elegir otras.")
    sel = sel.iloc[:30]
fondos = fondos[fondos.codigo_cnv.isin(sel.codigo_cnv)]
modo = st.radio("Qué comparar", ["R", "P", "R y P"], horizontal=True,
                format_func=lambda x: {"R": "Rendimiento (R)", "P": "Patrimonio (P)", "R y P": "R y P"}[x])
if 'comparador_llevar' not in st.session_state:
    st.session_state['comparador_llevar'] = st.session_state.get('comparador_llevar_memoria', False)
llevar = st.toggle("Llevar estos fondos al Informe descriptivo", key="comparador_llevar")
st.session_state['comparador_llevar_memoria'] = llevar
if llevar:
    calculos.enviar_al_informe(st.session_state, fondos.codigo_cnv)
else:
    st.session_state.pop(calculos.SELECCION_INFORME, None)

if 'comparador_llevar_gestion' not in st.session_state:
    st.session_state['comparador_llevar_gestion'] = st.session_state.get('comparador_llevar_gestion_memoria', False)
llevar_gestion = st.toggle('Llevar estos fondos a Gestión y eficiencia', key='comparador_llevar_gestion')
st.session_state['comparador_llevar_gestion_memoria'] = llevar_gestion
if llevar_gestion:
    calculos.enviar_a_gestion(st.session_state, fondos.codigo_cnv)
else:
    st.session_state.pop(calculos.SELECCION_GESTION, None)

cache = st.session_state.setdefault("consulta_puntual", {})
def cargar(fondo):
    with st.spinner(f"Cargando… {fondo['nombre']}"):
        return consulta_puntual.cargar(fondo, cache, consulta_puntual.token_configurado(st.secrets))

seleccion = (tuple(fondos.codigo_cnv), tuple(sel.codigo_cafci))
if fondos.seguido.all():     # los no seguidos se cargan en la sesión (consulta puntual): no se memorizan
    ser, notas = rapidez.memorizar("comparador_historia", lambda: calculos.historia_comparador(
        fondos, sel, datos.serie, cargar), *seleccion)
    notas = list(notas)
else:
    ser, notas = calculos.historia_comparador(fondos, sel, datos.serie, cargar)
desde = hasta = per = None
if (~fondos.seguido).any():
    notas.insert(0, consulta_puntual.LEYENDA)
graficos = []
serie_pdf = ser
controles = calculos.controles_fondos_comparador(st.session_state, sel)
visibles = sel[sel.codigo_cafci.isin([c["codigo"] for c in controles if c["visible"]])]
serie_pdf = ser[ser.codigo_cafci.isin(visibles.codigo_cafci)] if not ser.empty else ser
mostrar = visibles[["etiqueta", "moneda"]].rename(columns={"etiqueta": "Clase", "moneda": "Moneda"})
barras_lugar = st.container()
if ser.empty:
    notas.append("Sin historia disponible para esta selección.")
else:
    ultimo, primero = ser.fecha.max(), ser.fecha.min()
    periodos = {"7 días": 7, "30 días": 30, "90 días": 90, "6 meses": 182, "12 meses": 365, "Todo": None}
    per = st.radio("Período", [*periodos, "Desde / Hasta"], index=1, horizontal=True)
    if per == "Desde / Hasta":
        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", value=max(primero, ultimo - timedelta(days=30)),
                              min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
        hasta = c2.date_input("Hasta", value=ultimo, min_value=primero, max_value=ultimo, format="DD/MM/YYYY")
        aviso = calculos.validar_periodo(desde, hasta)
        if aviso:
            st.warning(aviso)
            st.stop()
    else:
        desde = primero if periodos[per] is None else max(primero, ultimo - timedelta(days=periodos[per]))
        hasta = ultimo
    st.caption(f"Desde {formato.fecha(desde)} hasta {formato.fecha(hasta)}")
    serie_pdf, resumen, graficos = rapidez.memorizar(
        "comparador_comparar", lambda: calculos.comparar(ser, desde, hasta, modo, controles),
        *seleccion, str(desde), str(hasta), modo, tuple((c["codigo"], c["visible"]) for c in controles),
        bool(fondos.seguido.all()))
    serie_pdf = serie_pdf[serie_pdf.codigo_cafci.isin(visibles.codigo_cafci)]
    mostrar = mostrar.merge(resumen, on=["Clase", "Moneda"], how="left")
    for indice, fig in enumerate(graficos):
        rapidez.grafico_memorizado(f'comparador_grafico_{indice}', formato.plotly_es(fig, fechas_x=True),
            *seleccion, str(desde), str(hasta), modo,
            tuple((c['codigo'], c['visible']) for c in controles), bool(fondos.seguido.all()))
    sin_cobertura = calculos.clases_sin_cobertura(serie_pdf, desde, hasta)
    sin_periodo = set(visibles.etiqueta) - set(serie_pdf.etiqueta)
    if sin_periodo:
        notas.append("Sin datos en el período: " + ", ".join(sorted(sin_periodo)) + ".")
    if sin_cobertura:
        notas.append("No tienen datos en todo el período: " + ", ".join(sin_cobertura) + ".")
aviso_moneda = calculos.aviso_monedas(visibles.moneda)
if aviso_moneda:
    notas.append(aviso_moneda)
if modo in ("P", "R y P") and (ser.empty or ser.patrimonio.isna().any()):
    notas.append("Patrimonio no disponible en algunas fechas o clases; los huecos no se interpolan.")
for nota in notas:
    st.warning(nota)
with barras_lugar:
    if visibles.empty:
        st.info('Encendé algún fondo para ver los valores por plazo.')
    else:
        for tipo in (['R', 'P'] if modo == 'R y P' else [modo]):
            titulo = 'Patrimonio promedio por plazo' if tipo == 'P' else 'Rendimiento por plazo'
            barras = rapidez.memorizar('comparador_barras_' + tipo,
                lambda: calculos.barras_comparador(ser, sel, controles, tipo, hasta), *seleccion,
                str(hasta), tuple((c['codigo'], c['visible']) for c in controles), bool(fondos.seguido.all()))
            st.caption(titulo + ' · ' + ('monto en la moneda de cada clase' if tipo == 'P' else '%')
                       + ' · primera clase seleccionada de cada fondo (ver detalle).')
            if tipo == 'P':
                for plazo, _ in calculos.PLAZOS_COMPARADOR:
                    faltantes = visibles.drop_duplicates('codigo_cnv')
                    faltantes = faltantes[~faltantes.codigo_cnv.isin(barras.loc[barras.Plazo.eq(plazo), 'codigo_cnv'])]
                    if not faltantes.empty:
                        st.caption('Sin patrimonio · ' + plazo + ': ' + ', '.join(faltantes.etiqueta) + '.')
            figura_barras = rapidez.memorizar('comparador_barras_figura_' + tipo,
                lambda: formato.grafico_barras_comparador(barras, tipo), *seleccion, str(hasta),
                tuple((c['codigo'], c['visible']) for c in controles), bool(fondos.seguido.all()))
            rapidez.grafico_memorizado('comparador_barras_grafico_' + tipo, figura_barras,
                *seleccion, str(hasta), tuple((c['codigo'], c['visible']) for c in controles), bool(fondos.seguido.all()))

tabla = formato.tabla(mostrar, fechas=[c for c in ("Desde", "Hasta", "Fecha del patrimonio") if c in mostrar],
                      enteros=[c for c in ("Datos", "Último valor de patrimonio") if c in mostrar],
                      numeros={"Rendimiento %": 2} if "Rendimiento %" in mostrar else {})
st.dataframe(formato.tabla_ordenable(mostrar,
    fechas=[c for c in ('Desde', 'Hasta', 'Fecha del patrimonio') if c in mostrar],
    enteros=[c for c in ('Datos', 'Último valor de patrimonio') if c in mostrar],
    numeros={'Rendimiento %': 2} if 'Rendimiento %' in mostrar else {}),
    hide_index=True, width="stretch")
rapidez.pdf_a_pedido(
    "comparador", (*seleccion, modo, str(desde), str(hasta), tuple((c["codigo"], c["visible"]) for c in controles),
                   tuple(notas)),
    lambda: informe.pdf_comparador(serie_pdf, tabla, modo, notas, fondos=fondos[fondos.codigo_cnv.isin(visibles.codigo_cnv)]),
    "comparador.pdf", "Descargar comparador en PDF")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades.")
st.caption(marca.actual()["texto_legal"])
