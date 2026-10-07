"""Sumar o quitar fondos: pedirle al robot que siga (o deje de seguir) un fondo del catálogo, y corregir su descripción y
temática. La app solo ANOTA el pedido; el robot lo atiende en su próxima corrida y acá se ve su estado."""
from datetime import datetime

import streamlit as st

import recarga
recarga.al_dia()          # si la app se publicó mientras estaba abierta, se leen los módulos nuevos (sin "Reboot")

import acceso  # noqa: E402
import calculos  # noqa: E402
import datos  # noqa: E402
import formato  # noqa: E402
import horarios  # noqa: E402

st.set_page_config(page_title="Sumar o quitar fondos", page_icon="📈", layout="wide")
acceso.requerir()
st.title("Sumar o quitar fondos")

corridas = horarios.cargar_procesador()
proxima = horarios.proxima_del_procesador(corridas, datetime.now(horarios.ARGENTINA))
st.caption(
    "Elegí un fondo del catálogo y pedí **seguirlo** o **dejar de seguirlo**. La app solo anota el pedido: el robot lo "
    f"atiende {horarios.texto_procesador(corridas)} (hora de Argentina) y, si no tiene pedidos, no hace nada. "
    f"**Próxima corrida: {horarios.texto_momento(proxima) if proxima else '—'}** (GitHub puede demorar unos minutos). "
    "Seguir un fondo carga su historia desde enero de 2024 y su cartera de la CNV.")

if "aviso_pedido" in st.session_state:       # lo que pasó con el pedido que se acaba de anotar (sobrevive al rerun)
    st.success(st.session_state.pop("aviso_pedido"))


def anotar(codigo, nombre, accion, descripcion=None, idea=None, id_ficha=None):
    """Valida, anota el pedido y vuelve a dibujar la pantalla con un aviso."""
    pedido, error = calculos.validar_pedido(accion, descripcion, idea, id_ficha)
    if error:
        st.error(error)
        return
    try:
        numero = datos.anotar_pedido(codigo, accion, pedido["descripcion"], pedido["idea"], pedido["id_ficha"])
    except datos.ErrorPedido as e:
        st.error(str(e))
        return
    st.session_state["aviso_pedido"] = (
        f"Pedido {numero} anotado: {calculos.ACCIONES_PEDIDO[accion].lower()} «{nombre}». Queda pendiente hasta la "
        "próxima corrida del robot.")
    st.rerun()


catalogo = datos.catalogo_fondos()
pedidos = calculos.preparar_pedidos(datos.pedidos_recientes())

# ───────── Buscador ─────────
st.subheader("Buscar un fondo")
texto = st.text_input("Nombre del fondo o de la gerente", placeholder="Por ejemplo: galileo, compass, premium...")
c1, c2, c3 = st.columns(3)
moneda = c1.radio("Moneda", ("Todas", *calculos.OPCIONES_MONEDA), horizontal=True)
situacion = c2.radio("Situación", ("Todos", "Seguidos", "No seguidos"), horizontal=True)
c3.caption(f"{int(catalogo['seguido'].sum())} fondos seguidos de {len(catalogo):,}".replace(",", "."))
c4, c5 = st.columns(2)
tipos = c4.multiselect("Tipo de fondo", sorted(catalogo["tipo_fondo"].dropna().unique()), placeholder="Todos",
                       help="Tipo de fondo de la CAFCI. Sin elegir ninguno: todos.")
rubros = c5.multiselect("Rubro", sorted(catalogo["rubro"].dropna().unique()), placeholder="Todos",
                        help="Rubro del fondo: por cartera (fondos seguidos) o igual al tipo de fondo. Sin elegir ninguno: todos.")
encontrados = calculos.filtrar_catalogo(catalogo, texto, moneda, tipos, rubros, situacion)
if encontrados.empty:
    st.info("Ningún fondo cumple esa búsqueda.")
else:
    LIMITE = 300
    mostrados = encontrados.head(LIMITE)
    st.caption((f"{len(encontrados):,} fondos encontrados" if len(encontrados) != 1 else "1 fondo encontrado").replace(",", ".")
               + (f" (se muestran los primeros {LIMITE}: afiná la búsqueda para ver el resto)." if len(encontrados) > LIMITE else "."))
    tabla = mostrados.assign(moneda_texto=mostrados["moneda"].map(calculos.NOMBRE_MONEDA).fillna(mostrados["moneda"]))
    tabla = tabla[["nombre", "gerente", "moneda_texto", "tipo_fondo", "rubro", "seguido"]].rename(columns={
        "nombre": "Fondo", "gerente": "Gerente", "moneda_texto": "Moneda", "tipo_fondo": "Tipo de fondo",
        "rubro": "Rubro", "seguido": "¿Se sigue?"})
    for columna in ("Gerente", "Tipo de fondo", "Rubro"):
        tabla[columna] = tabla[columna].map(formato.texto_o_guion)
    st.dataframe(formato.tabla(tabla, booleanos=["¿Se sigue?"]), hide_index=True, width="stretch", height=260)

    # ───────── El fondo elegido ─────────
    por_codigo = mostrados.set_index("codigo_cnv")
    codigo = st.selectbox("Fondo elegido", list(por_codigo.index), key="fondo_elegido",
                          format_func=lambda c: f"{por_codigo.loc[c, 'nombre']} — {por_codigo.loc[c, 'gerente'] or 'sin gerente'}"
                                                f"{' (se sigue)' if por_codigo.loc[c, 'seguido'] else ''}")
    f = por_codigo.loc[codigo]
    st.subheader(f["nombre"])
    st.caption(f"{f['gerente'] or '—'} · {calculos.NOMBRE_MONEDA.get(f['moneda'], f['moneda'])} · "
               f"{formato.texto_o_guion(f['tipo_fondo'])} · Rubro: {formato.texto_o_guion(f['rubro'])}")
    propios = pedidos[pedidos["codigo_cnv"] == codigo]
    hay_pendiente = bool((propios["estado"] == "pendiente").any())
    if hay_pendiente:
        st.warning("Este fondo ya tiene un pedido pendiente: esperá a que el robot lo atienda para pedir otra cosa.")

    if not f["seguido"]:
        st.write("**No se sigue.** Si lo seguís, el robot carga su historia y su cartera, y aparece en las demás pantallas.")
        with st.form(f"seguir_{codigo}", clear_on_submit=False):
            descripcion = st.text_area("Descripción breve (opcional)", max_chars=calculos.MAX_DESCRIPCION,
                                       help="Una línea sobre el fondo. Si la dejás vacía, las pantallas muestran «—».")
            idea = st.text_area("Idea y temática (opcional)", max_chars=calculos.MAX_IDEA,
                                help="La idea de inversión del fondo. Si la dejás vacía, las pantallas muestran «—».")
            id_ficha = st.text_input("ID de la ficha CNV (opcional)", placeholder="Por ejemplo: 63491",
                                     help="El robot lo busca solo. Si no lo encuentra, el pedido queda con problema y te lo "
                                          "pide más abajo, en «Mis pedidos». Si ya lo sabés, cargalo acá y lo usa sin buscar.")
            if st.form_submit_button("Seguir este fondo", type="primary", disabled=hay_pendiente):
                anotar(codigo, f["nombre"], "seguir", descripcion, idea, id_ficha)
    else:
        st.write("**Se sigue.**")
        st.markdown(f"**Descripción breve:** {formato.texto_o_guion(f['descripcion_breve'])}  \n"
                    f"**Idea y temática:** {formato.texto_o_guion(f['tematica'])}"
                    + ("  \n*(provisorio)*" if f["tematica_provisoria"] else ""))
        with st.form(f"editar_{codigo}"):
            st.markdown("**Editar descripción y temática**")
            descripcion = st.text_area("Descripción breve", value=f["descripcion_breve"] or "",
                                       max_chars=calculos.MAX_DESCRIPCION)
            idea = st.text_area("Idea y temática", value=f["tematica"] or "", max_chars=calculos.MAX_IDEA)
            st.caption("Gana lo último que se cargó. Si dejás un campo vacío, se borra.")
            if st.form_submit_button("Guardar descripción y temática", disabled=hay_pendiente):
                anotar(codigo, f["nombre"], "editar_tematica", descripcion, idea)
        if st.button("Dejar de seguir este fondo", key=f"dejar_{codigo}", disabled=hay_pendiente):
            anotar(codigo, f["nombre"], "dejar")
        st.caption("Dejar de seguir **no borra nada**: su historia de valores, sus rendimientos y sus carteras quedan "
                   "guardados. Solo deja de actualizarse y sale de las pantallas de fondos seguidos.")

# ───────── Mis pedidos ─────────
st.subheader("Mis pedidos")
if pedidos.empty:
    st.info("Todavía no se pidió nada.")
else:
    lista = pedidos.rename(columns={"id": "Pedido", "fondo": "Fondo", "accion_texto": "Qué se pidió", "estado_texto": "Estado",
                                    "motivo": "Motivo o nota", "pedido_en": "Pedido el", "procesado_en": "Atendido el"})
    lista["Pedido"] = lista["Pedido"].astype(str)
    lista["Motivo o nota"] = lista["Motivo o nota"].map(formato.texto_o_guion)
    lista["Fondo"] = lista["Fondo"].map(formato.texto_o_guion)
    st.dataframe(formato.tabla(lista[["Pedido", "Fondo", "Qué se pidió", "Estado", "Motivo o nota", "Pedido el", "Atendido el"]],
                               fechas_hora_ar=["Pedido el", "Atendido el"]), hide_index=True, width="stretch")
    st.caption("Horas en hora de Argentina. «Pendiente»: el robot todavía no lo atendió. «Listo»: ya se hizo. "
               "«Problema»: no se pudo; el motivo dice por qué.")

    reintentables = lista[lista["reintentable"]]
    if not reintentables.empty:
        st.markdown("**Reintentar con el ID de la ficha CNV**")
        st.caption("Estos pedidos quedaron con problema porque el robot no pudo encontrar o validar la ficha del fondo en la "
                   "CNV. Buscá el fondo en la web de la CNV (Fondos Comunes de Inversión): el ID es el número que figura en "
                   "la dirección de su ficha. Se anota un pedido nuevo con ese ID (el original queda como está).")
        with st.form("reintentar"):
            elegido = st.selectbox("Pedido con problema", list(reintentables.index), key="reintento",
                                   format_func=lambda i: f"Pedido {reintentables.loc[i, 'Pedido']}: {reintentables.loc[i, 'Fondo']}")
            id_nuevo = st.text_input("ID de la ficha CNV", placeholder="Por ejemplo: 63491")
            if st.form_submit_button("Reintentar con este ID"):
                if not id_nuevo.strip():
                    st.error("Escribí el ID de la ficha CNV.")
                else:
                    p = reintentables.loc[elegido]
                    anotar(int(p["codigo_cnv"]), p["Fondo"], "seguir", p["descripcion_breve"], p["idea_tematica"], id_nuevo)
