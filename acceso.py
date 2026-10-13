"""Acceso de usuarios, sesiones y administración; Auth solo desde el servidor."""
import hmac
import json
import os
import secrets
import time
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import streamlit as st


class ErrorAcceso(Exception):
    """Mensaje fijo y seguro: nunca incluye respuestas ni secretos de Auth."""


def secreto(nombre):
    try:
        valor = st.secrets[nombre]
    except Exception:
        valor = None
    return valor or os.environ.get(nombre)


def _clave():
    return secreto("APP_PASSWORD")


def _api(metodo, ruta, cuerpo=None, token=None, admin=False):
    if os.environ.get("ROBOT_FCI_SIN_RED") == "1":
        raise ErrorAcceso("Auth deshabilitado en la verificación sin red.")
    url = secreto("SUPABASE_URL")
    clave = secreto("SUPABASE_SERVICE_KEY" if admin else "SUPABASE_ANON_KEY")
    if not url or not clave or not str(url).startswith("https://"):
        raise ErrorAcceso("Falta configurar el acceso de usuarios en el servidor.")
    peticion = Request(str(url).rstrip("/") + "/auth/v1/" + ruta,
                       data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                       headers={"apikey": clave, "Authorization": "Bearer " + (token or clave),
                                "Content-Type": "application/json"}, method=metodo)
    try:
        with urlopen(peticion, timeout=20) as respuesta:
            texto = respuesta.read()
        return json.loads(texto) if texto else {}
    except Exception:
        raise ErrorAcceso("No se pudo completar la operación. Revisá los datos o consultá al administrador.") from None


def _identidad(usuario):
    meta = usuario.get("app_metadata", {})
    try:
        identificador = str(UUID(usuario["id"]))
    except (KeyError, ValueError, TypeError):
        raise ErrorAcceso("Usuario sin acceso autorizado.") from None
    if not meta.get("organizacion") or meta.get("rol") not in ("admin", "asesor"):
        raise ErrorAcceso("Usuario sin acceso autorizado.")
    return {"id": identificador, "correo": usuario.get("email", ""),
            "organizacion": meta["organizacion"], "rol": meta["rol"],
            "cambio_obligatorio": bool(meta.get("cambio_obligatorio", True)),
            "version_clave": meta.get("version_clave")}


def ingresar(correo, clave):
    if not _api("GET", "settings").get("disable_signup", False):
        raise ErrorAcceso("El administrador debe desactivar el registro público en Supabase antes de habilitar usuarios.")
    sesion = _api("POST", "token?grant_type=password", {"email": correo.strip(), "password": clave})
    persona = _identidad(sesion["user"])
    st.session_state["auth"] = {"access_token": sesion["access_token"],
                                "refresh_token": sesion["refresh_token"],
                                "vence": time.time() + sesion.get("expires_in", 3600),
                                "persona": persona}
    st.session_state.pop("acceso_ok", None)
    return persona


def usuario():
    sesion = st.session_state.get("auth")
    return sesion["persona"] if sesion else None


def _validar_sesion():
    sesion = st.session_state.get("auth")
    if not sesion:
        raise ErrorAcceso("Ingresá con tu usuario.")
    if time.time() >= sesion["vence"] - 60:
        nueva = _api("POST", "token?grant_type=refresh_token", {"refresh_token": sesion["refresh_token"]})
        sesion.update(access_token=nueva["access_token"], refresh_token=nueva["refresh_token"],
                      vence=time.time() + nueva.get("expires_in", 3600))
    persona = _identidad(_api("GET", "user", token=sesion["access_token"]))
    if persona["version_clave"] != sesion["persona"]["version_clave"]:
        cerrar_sesion()
        raise ErrorAcceso("Tu clave fue restablecida. Volvé a ingresar.")
    sesion["persona"] = persona
    return persona


def cerrar_sesion():
    sesion = st.session_state.get("auth")
    try:
        if sesion:
            _api("POST", "logout?scope=local", token=sesion["access_token"])
    except ErrorAcceso:
        pass
    finally:
        st.session_state.clear()


def _admin():
    persona = _validar_sesion()
    if persona["rol"] != "admin" or persona["cambio_obligatorio"]:
        raise ErrorAcceso("Solo el administrador puede gestionar usuarios.")
    return persona


def crear_usuario(correo, admin=False, inicial=False):
    # inicial es exclusivamente para el comando local del operador del servidor.
    organizacion = "propia" if inicial else _admin()["organizacion"]
    temporal = secrets.token_urlsafe(24)
    _api("POST", "admin/users", {"email": correo.strip(), "password": temporal, "email_confirm": True,
                                 "app_metadata": {"organizacion": organizacion, "rol": "admin" if admin else "asesor",
                                                  "cambio_obligatorio": True, "version_clave": str(uuid4())}}, admin=True)
    return temporal


def listar_usuarios():
    persona = _admin()
    salida = []
    pagina = 1
    while True:
        usuarios = _api("GET", f"admin/users?page={pagina}&per_page=100", admin=True).get("users", [])
        salida.extend({"id": u["id"], "correo": u.get("email", ""), "rol": u["app_metadata"].get("rol", "asesor")}
                      for u in usuarios if u.get("app_metadata", {}).get("organizacion") == persona["organizacion"])
        if len(usuarios) < 100:
            return salida
        pagina += 1


def restablecer_clave(identificador):
    persona = _admin()
    identificador = str(UUID(identificador))
    destino = _api("GET", "admin/users/" + identificador, admin=True)
    meta = destino.get("app_metadata", {})
    if meta.get("organizacion") != persona["organizacion"]:
        raise ErrorAcceso("El usuario no pertenece a tu organización.")
    temporal = secrets.token_urlsafe(24)
    _api("PUT", "admin/users/" + identificador,
         {"password": temporal, "app_metadata": {**meta, "cambio_obligatorio": True,
                                                   "version_clave": str(uuid4())}}, admin=True)
    return temporal


def cambiar_clave(clave, confirmacion):
    persona = _validar_sesion()
    if len(clave) < 12 or clave != confirmacion:
        raise ErrorAcceso("Usá al menos 12 caracteres y repetí la misma clave.")
    _api("PUT", "user", {"password": clave}, token=st.session_state["auth"]["access_token"])
    destino = _api("GET", "admin/users/" + persona["id"], admin=True)
    meta = destino["app_metadata"]
    if meta.get("version_clave") != persona["version_clave"]:
        cerrar_sesion()
        raise ErrorAcceso("Tu clave fue restablecida. Volvé a ingresar.")
    _api("PUT", "admin/users/" + persona["id"], {"app_metadata": {**meta, "cambio_obligatorio": False}}, admin=True)
    st.session_state["auth"]["persona"]["cambio_obligatorio"] = False


def _pantalla_usuarios():
    with st.sidebar.expander("Usuarios"):
        try:
            with st.form("crear_usuario", clear_on_submit=True):
                correo = st.text_input("Correo del nuevo usuario")
                admin = st.checkbox("Administrador de la organización")
                crear = st.form_submit_button("Crear usuario")
            if crear:
                st.caption("Clave temporal: pasala a la persona. Se muestra una sola vez.")
                st.code(crear_usuario(correo, admin), language=None)
            if st.button("Ver usuarios"):
                st.session_state["mostrar_usuarios"] = True
            if st.session_state.get("mostrar_usuarios"):
                for persona in listar_usuarios():
                    st.write(persona["correo"] + " · " + persona["rol"])
                    if st.button("Restablecer clave", key="restablecer_" + persona["id"]):
                        st.caption("Clave temporal: se muestra una sola vez.")
                        st.code(restablecer_clave(persona["id"]), language=None)
        except ErrorAcceso as error:
            st.error(str(error))


def indice_moneda(opciones):
    persona = usuario()
    if not persona or persona["cambio_obligatorio"]:
        return 0
    valor = st.session_state.get("moneda_defecto", "Pesos")
    return list(opciones).index(valor) if valor in opciones else 0


def preferencias_inicio():
    if not usuario():
        return
    with st.expander("Preferencias"):
        opciones = ("Pesos", "Dólares")
        with st.form("preferencia_moneda"):
            valor = st.selectbox("Moneda por defecto", opciones, index=indice_moneda(opciones))
            guardar = st.form_submit_button("Guardar preferencia")
        if guardar:
            import datos
            try:
                persona = _validar_sesion()
                datos.guardar_preferencia(persona, "moneda", valor)
                st.session_state["moneda_defecto"] = valor
                st.success("Preferencia guardada.")
            except (ErrorAcceso, datos.ErrorPreferencias):
                st.error("No se pudo guardar la preferencia.")


def requerir():
    """Único control de todas las pantallas; exige cambiar la clave antes de mostrar datos."""
    import marca
    if st.session_state.get("auth"):
        try:
            persona = _validar_sesion()
        except ErrorAcceso as error:
            cerrar_sesion()
            st.error(str(error))
            st.stop()
        if st.sidebar.button("Cerrar sesión"):
            cerrar_sesion()
            st.rerun()
        if persona["cambio_obligatorio"]:
            st.title("Cambiar clave temporal")
            with st.form("cambiar_clave", clear_on_submit=True):
                clave = st.text_input("Nueva clave (12 caracteres o más)", type="password")
                repetir = st.text_input("Repetí la nueva clave", type="password")
                cambiar = st.form_submit_button("Cambiar clave")
            if cambiar:
                try:
                    cambiar_clave(clave, repetir)
                    st.rerun()
                except ErrorAcceso as error:
                    st.error(str(error))
            st.stop()
        try:
            import datos
            st.session_state["marca"] = datos.organizacion(persona)
            if "moneda_defecto" not in st.session_state:
                st.session_state["moneda_defecto"] = datos.leer_preferencia(persona, "moneda") or "Pesos"
        except datos.ErrorPreferencias:
            st.error("No se pudo cargar la organización y sus preferencias.")
            st.stop()
        marca.mostrar()
        if persona["rol"] == "admin":
            _pantalla_usuarios()
        return
    if st.session_state.get("acceso_ok"):
        if _clave():
            st.sidebar.warning("Acceso con clave compartida de respaldo.")
            if st.sidebar.button("Cerrar sesión"):
                cerrar_sesion()
                st.rerun()
            return
        cerrar_sesion()
    # Las pruebas de pantallas sin red usan datos simulados; en producción se falla cerrado.
    if os.environ.get("ROBOT_FCI_SIN_RED") == "1" and not _clave() and not secreto("SUPABASE_URL") and not secreto("SUPABASE_ANON_KEY"):
        return
    st.title(marca.nombre())
    st.caption("Acceso restringido.")
    if secreto("SUPABASE_ANON_KEY"):
        with st.form("ingreso_usuario", clear_on_submit=True):
            correo = st.text_input("Correo")
            clave = st.text_input("Clave", type="password")
            enviar = st.form_submit_button("Entrar")
        if enviar:
            try:
                ingresar(correo, clave)
                st.rerun()
            except ErrorAcceso:
                st.error("No se pudo ingresar. Revisá el correo y la clave.")
    else:
        st.info("El ingreso por usuario espera la configuración del servidor.")
    with st.expander("Olvidé mi clave"):
        st.write("Pedile a tu administrador que restablezca tu clave.")
    compartida = _clave()
    if compartida:
        st.warning("La clave compartida sigue disponible como respaldo durante la transición.")
        with st.form("acceso", clear_on_submit=True):
            intento = st.text_input("Clave de acceso compartida", type="password")
            enviar = st.form_submit_button("Entrar con respaldo")
        if enviar:
            if hmac.compare_digest(intento.encode(), str(compartida).encode()):
                st.session_state["acceso_ok"] = True
                st.rerun()
            time.sleep(2)
            st.error("Clave incorrecta.")
    st.stop()
