"""Comparador: filtros comunes, selección por casillas, R/P y PDF."""
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

st.button("Restablecer", on_click=filtros.restablecer_comparador, args=(st.session_state,))

todos = datos.fondos_ficha(incluir_no_seguidos=True)
if todos.empty:
    st.info("No hay fondos en el catálogo.")
    st.stop()
base = filtros.mostrar(todos, clave="comparador", persistente=True, inicial=inicial_seguimiento, multiple=True)
if base.empty:
    st.info("No hay fondos para estos filtros.")
    st.stop()
nombres = base.set_index("codigo_cnv")["nombre"].to_dict()
with st.expander("Fondos", expanded=False):
    elegidos = filtros.lista_fondos("Fondos", nombres, "comparador_fondos", vacio_todos=False)
if not elegidos:
    with st.expander("Clase", expanded=False):
        st.info("Seleccioná fondos para ver sus clases.")
    st.info("Elegí al menos un fondo.")
    st.stop()
fondos = base[base.codigo_cnv.isin(elegidos)]
monedas = tuple(st.session_state['comparador_filtro_1'])
clases = rapidez.memorizar('comparador_catalogo_clases',
    lambda: calculos.clases_comparador(fondos, list(monedas), datos.clases_de_fondo),
    tuple(fondos.codigo_cnv), monedas)
if clases.empty:
    st.info("No hay clases disponibles para estos fondos y moneda.")
    st.stop()
preferidas = []
for _, grupo in clases.groupby("codigo_cnv"):
    preferidas += datos.clases_preferidas(grupo).codigo_cafci.tolist()
etiquetas = clases.set_index("codigo_cafci").etiqueta.to_dict()
fondo_de = clases.set_index("codigo_cafci").codigo_cnv.to_dict()
with st.expander("Clase", expanded=False):
    elegidas = filtros.casillas("Clase", clases.codigo_cafci.tolist(), "comparador_clases",
                               todas="Todas", formato=etiquetas.get, preferidas=preferidas, persistente=True,
                               al_desmarcar=lambda c, mem: filtros.quitar_fondo_sin_clases(
                                   st.session_state, c, mem, fondo_de, set(preferidas)))
if not elegidas:
    st.info("Elegí al menos una clase.")
    st.stop()
if len(elegidas) > 30:
    st.warning("Se muestran las primeras 30 clases como máximo. Afiná la selección para elegir otras.")
    elegidas = elegidas[:30]
sel = clases[clases.codigo_cafci.isin(elegidas)]
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
controles = calculos.controles_series(st.session_state, sel)
opciones_series = {c['codigo']: c['corto'] for c in controles}
st.session_state.pop('comparador_series_elegidas', None)    # clave del multiselect de U103
anteriores = st.session_state.get('comparador_series_pills')
if not isinstance(anteriores, list):
    st.session_state.pop('comparador_series_pills', None)
    anteriores = None
else:
    nuevas = set(opciones_series) - set(st.session_state.get('comparador_series_disponibles', []))
    st.session_state['comparador_series_pills'] = [c for c in opciones_series if c in anteriores or c in nuevas]
series_elegidas = st.pills('Series en el gráfico', list(opciones_series), selection_mode='multi',
    default=list(opciones_series) if anteriores is None else None,
    format_func=opciones_series.get, key='comparador_series_pills',
    help='Tocá una serie para sacarla o volver a ponerla en el gráfico, la tabla y el PDF. '
         + ' | '.join(c['nombre'] for c in controles)) or []
st.session_state['comparador_series_disponibles'] = list(opciones_series)
for c in controles:
    c['visible'] = c['codigo'] in series_elegidas
    st.session_state['comparador_series'][c['codigo']]['visible'] = c['visible']
visibles = sel[sel.codigo_cafci.isin([c["codigo"] for c in controles if c["visible"]])]
serie_pdf = ser[ser.codigo_cafci.isin(visibles.codigo_cafci)] if not ser.empty else ser
mostrar = visibles[["etiqueta", "moneda"]].rename(columns={"etiqueta": "Clase", "moneda": "Moneda"})
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
tabla = formato.tabla(mostrar, fechas=[c for c in ("Desde", "Hasta", "Fecha del patrimonio") if c in mostrar],
                      enteros=[c for c in ("Datos", "Último valor de patrimonio") if c in mostrar],
                      numeros={"Rendimiento %": 2} if "Rendimiento %" in mostrar else {})
st.dataframe(tabla, hide_index=True, width="stretch")
rapidez.pdf_a_pedido(
    "comparador", (*seleccion, modo, str(desde), str(hasta), tuple((c["codigo"], c["visible"]) for c in controles),
                   tuple(notas)),
    lambda: informe.pdf_comparador(serie_pdf, tabla, modo, notas, fondos=fondos[fondos.codigo_cnv.isin(visibles.codigo_cnv)]),
    "comparador.pdf", "Descargar comparador en PDF")
st.caption("Rendimientos sobre la cuotaparte, sin considerar distribución de utilidades.")
st.caption(marca.actual()["texto_legal"])
