"""Gestión y eficiencia: costos, patrimonio, calificación y rendimiento a 12 meses de las clases de los fondos, con un
puntaje de eficiencia informativo (70% rendimiento 12m + 30% honorario de la sociedad gerente)."""
import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402

st.set_page_config(page_title="Gestión y eficiencia", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Gestión y eficiencia")
st.caption("Todos los fondos del catálogo de la CAFCI. Los datos son los de la última planilla diaria; "
           "el rendimiento a 12 meses ya es neto de honorarios y gastos.")

todas = datos.clases_gestion()
if todas.empty:
    st.info("Todavía no hay datos de clases.")
    st.stop()

# ───────── Filtros ─────────
moneda = st.radio("Moneda", calculos.OPCIONES_MONEDA, horizontal=True,
                  help="Obligatoria: los patrimonios no se comparan entre monedas. Dólares incluye USD y USD billete.")
base = calculos.filtrar_moneda(todas, moneda)
c1, c2 = st.columns(2)
tipos = c1.multiselect("Tipo de fondo", sorted(base["tipo_fondo"].dropna().unique()), placeholder="Todos",
                       help="Es el tipo de fondo de la CAFCI (existe para todos los fondos). Sin elegir ninguno: todos.")
if tipos:
    base = base[base["tipo_fondo"].isin(tipos)]
rubros = c2.multiselect("Rubro", sorted(base["rubro"].dropna().unique()), placeholder="Todos",
                        help="Rubro del fondo: por cartera (fondos seguidos) o igual al tipo de fondo. Sin elegir ninguno: todos.")
if rubros:
    base = base[base["rubro"].isin(rubros)]
fondos = sorted(base["fondo"].unique())
if not fondos:
    st.info("Ningún fondo cumple esos filtros.")
    st.stop()
elegidos = st.multiselect(f"Fondos incluidos ({len(fondos)} cumplen los filtros)", fondos, default=fondos,
                          help="Sacá los que no quieras o dejá solo los que quieras comparar.")
if not elegidos:
    st.info("Elegí al menos un fondo.")
    st.stop()
ver_todas = st.checkbox("Ver todas las clases", value=False,
                        help="Por defecto se muestran las clases A y B de cada fondo (o las que haya si no tiene A ni B).")
clases = calculos.orden_inicial(calculos.clases_a_mostrar(base[base["fondo"].isin(elegidos)], ver_todas))

# ───────── Ejecutar eficiencia (se apaga si cambia cualquier filtro) ─────────
firma = (moneda, tuple(tipos), tuple(rubros), tuple(elegidos), ver_todas)
if st.session_state.get("eficiencia_firma") != firma:
    st.session_state["eficiencia_firma"] = firma
    st.session_state["eficiencia_activa"] = False
if st.button("Ejecutar eficiencia", type="primary",
             help="Agrega el puntaje de eficiencia (0 a 100) y la posición, y ordena por puntaje."):
    st.session_state["eficiencia_activa"] = True
activa = st.session_state["eficiencia_activa"]

# ───────── Tabla de clases ─────────
PRINCIPALES = {"gerente": "Gerente", "clase": "Fondo y clase", "honorarios_sg": "Honorario gerente (% anual)",
               "honorarios_sd": "Honorario depositaria (% anual)", "patrimonio": "Patrimonio",
               "calificacion": "Calificación de riesgo", "rend_12m": "Rendimiento 12 meses (%)"}
INFORMACION = {"gastos_ordinarios": "Gastos ordinarios (%)", "comision_ingreso": "Comisión de ingreso (%)",
               "comision_rescate": "Comisión de rescate (%)", "comision_transferencia": "Comisión de transferencia (%)",
               "honorarios_exito": "Honorario de éxito"}
CORTOS = ["Honorario gerente (% anual)", "Honorario depositaria (% anual)", "Gastos ordinarios (%)",
          "Comisión de ingreso (%)", "Comisión de rescate (%)", "Comisión de transferencia (%)"]


def armar(df, extra):
    """Columnas en el orden pedido: las 7 principales, lo agregado, y los demás costos solo como información."""
    out = df.rename(columns={**PRINCIPALES, **INFORMACION})
    out["Honorario de éxito"] = out["Honorario de éxito"].map({"S": "Sí", "N": "No"})
    out["Estado"] = out["marca_estado"].replace("", None)
    return out[[*PRINCIPALES.values(), *extra, *INFORMACION.values(), "Estado"]]


def mostrar(df, numeros_extra=None, enteros_extra=()):
    estilo = formato.tabla_ordenable(
        df, numeros={"Rendimiento 12 meses (%)": 2, **(numeros_extra or {})},
        enteros=["Patrimonio", *enteros_extra], cortos={c: (2, 4) for c in CORTOS if c in df.columns})
    st.dataframe(estilo, hide_index=True, width="stretch")


if not activa:
    st.subheader(f"Clases ({formato.entero(len(clases))})")
    st.caption("Ordenadas de mayor a menor honorario de la sociedad gerente. Tocá el encabezado de cualquier columna "
               "para reordenar. Los demás costos son solo informativos.")
    t = armar(clases.assign(**{"Tipo de fondo": clases["tipo_fondo"]}), ["Tipo de fondo"])
    mostrar(t)
else:
    ranking, afuera = calculos.ejecutar_eficiencia(clases)
    st.subheader(f"Ranking de eficiencia ({formato.entero(len(ranking))} clases comparadas)")
    if ranking.empty:
        st.warning("Ninguna clase de las elegidas se puede comparar (mirá los motivos más abajo).")
    else:
        ranking = ranking.assign(**{
            "Puntaje de eficiencia (0-100)": ranking["puntaje"], "Posición": ranking["posicion"],
            "Grupo de comparación": ranking["grupo"] + " (" + ranking["en_grupo"].astype(str) + " clases)"})
        t = armar(ranking, ["Puntaje de eficiencia (0-100)", "Posición", "Grupo de comparación"])
        st.caption("Ordenadas por puntaje. La posición es dentro de cada grupo (mismo tipo de fondo y moneda).")
        mostrar(t, numeros_extra={"Puntaje de eficiencia (0-100)": 1}, enteros_extra=["Posición"])
    st.subheader(f"Quedan afuera del ranking ({formato.entero(len(afuera))} clases)")
    if afuera.empty:
        st.caption("Todas las clases de la tabla entran al ranking.")
    else:
        resumen = afuera["motivo"].value_counts()
        st.caption(" · ".join(f"{m}: {formato.entero(n)}" for m, n in resumen.items()))
        mostrado = afuera.rename(columns={"gerente": "Gerente", "clase": "Fondo y clase", "motivo": "Motivo"})
        st.dataframe(formato.tabla(mostrado[["Gerente", "Fondo y clase", "Motivo"]]), hide_index=True, width="stretch")
    st.markdown(
        "**Cómo se calculó**\n\n"
        "- Cada clase se compara **solo con las clases del mismo tipo de fondo y de la misma moneda** que están en la "
        "tabla de arriba (si sacás fondos o cambiás de clases, el ranking se recalcula al volver a tocar el botón).\n"
        "- Dentro de cada grupo, a cada clase se le da un número de 0 a 100 según el lugar que ocupa: **0 es el peor y "
        "100 es el mejor**.\n"
        "- El **rendimiento a 12 meses** vale **70%** (cuanto más alto, mejor) y el **honorario de la sociedad gerente** "
        "vale **30%** (cuanto más bajo, mejor). El puntaje es la suma de las dos partes.\n"
        "- Los demás costos (honorario de la depositaria, gastos, comisiones y honorario de éxito) se muestran solo como "
        "información y **no cambian el puntaje**.\n"
        "- Quedan afuera las clases sin 12 meses de historia, las que tienen algún estado (sin patrimonio, sin datos "
        "recientes, fuera de la planilla, con marca), las que informan **honorario de la sociedad gerente 0%** (puede ser "
        "un dato faltante: verificar), las de **rendimiento atípico** (muy lejos del resto de su grupo: se pasa del cuarto "
        "más alto, o queda por debajo del cuarto más bajo, por más de 3 veces el ancho de la franja central de "
        "rendimientos del grupo; solo se controla en grupos de 8 clases o más: verificar) y los grupos con menos de 3 clases. Los percentiles se calculan sin "
        "ellas; siguen visibles en la tabla de arriba.")

st.caption(calculos.DESCARGO_EFICIENCIA)
