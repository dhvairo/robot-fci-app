"""Acceso de usuarios, sesiones y administración; Auth solo desde el servidor."""
import re
import json
import os
import secrets
import time
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import streamlit as st


# Configuración exclusiva del servidor. U46 no instala backend ni registro productivos.
LECTURA_SEGUIMIENTO = {"activa": False, "backend": None, "registro": {}}

# Misma convención de interruptor privado; solo configuración del servidor.
PREFERENCIAS_USUARIO = {"activa": False, "backend": None}

ROLES_PERMISOS = {'activa': False, 'backend': None}


def _estado_rerun():
    """Memoria del ScriptRunContext; cursors se reemplaza al comenzar cada rerun."""
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    ctx = get_script_run_ctx(suppress_warning=True)
    if ctx is None:
        return None
    anterior = getattr(ctx, '_seguimiento_rerun', None)
    if anterior is None or anterior[0] is not ctx.cursors:
        anterior = (ctx.cursors, {})
        ctx._seguimiento_rerun = anterior
        import logging
        logging.getLogger('seguimiento').log(logging.WARNING, 'SEGUIMIENTO RERUN INICIO')
    return anterior[1]


def lectura_privada(clave, contexto, leer, copiar=True):
    """Copia de sesión por 5 minutos, después de validar Auth; jamás caché global.

    copiar=False entrega el objeto guardado (solo lectura) para no copiar estructuras grandes en cada clic."""
    from copy import deepcopy
    if _estado_rerun() is None:
        return leer()
    identidad = tuple(sorted(contexto.items()))
    cache = st.session_state.setdefault('_lecturas_privadas', {})
    llave = (clave, identidad)
    ahora = time.monotonic()
    inicio = time.perf_counter()
    for k in list(cache):
        if ahora - cache[k][0] >= 300:
            del cache[k]
    acierto = llave in cache
    if llave not in cache:
        resultado = leer()  # No guardar fallos ni operaciones sin commit.
        if len(cache) >= 12:
            cache.clear()
        cache[llave] = (ahora, deepcopy(resultado))
    resultado = deepcopy(cache[llave][1]) if copiar else cache[llave][1]
    tipo = clave[0] if isinstance(clave, tuple) else clave
    nombre = {'vista_carteras': 'CLASIFICACION', 'clasificaciones': 'CLASIFICACION',
              'seguimiento': 'SEGUIMIENTO', 'marca': 'MARCA',
              'preferencia': 'PERSONAL'}.get(tipo, 'PRIVADA')
    registrar_cache(nombre, acierto, (time.perf_counter() - inicio) * 1000)
    return resultado


def memoria_derivada(llave, calcular):
    """Resultado derivado (tablas, series, PDF) guardado por sesión 5 minutos.

    La llave la arma quien llama con organización, usuario y selección livianas; se descarta junto con las
    lecturas privadas al escribir. Los resultados guardados se tratan como de solo lectura.
    """
    if _estado_rerun() is None:
        return calcular()
    cache = st.session_state.setdefault('_derivados', {})
    ahora = time.monotonic()
    for k in [k for k, (t, _) in cache.items() if ahora - t >= 300]:
        del cache[k]
    if llave not in cache:
        valor = calcular()
        while len(cache) >= 40:
            del cache[next(iter(cache))]
        cache[llave] = (ahora, valor)
    return cache[llave][1]


def invalidar_lecturas_privadas():
    st.session_state.pop('_arranque_privado', None)
    st.session_state.pop('_lecturas_privadas', None)
    st.session_state.pop('_derivados', None)
    st.session_state.pop('preferencias_usuario_cache', None)


def registrar_cache(nombre, acierto, ms=0):
    """Solo nombres internos, duración y contadores; ninguna identidad ni parámetro."""
    import logging
    estado = _estado_rerun()
    if estado is not None:
        contador = 'cache_aciertos' if acierto else 'cache_fallos'
        estado[contador] = estado.get(contador, 0) + 1
    logging.getLogger('seguimiento').log(logging.WARNING,
        'SEGUIMIENTO CACHE %s %s %.3f_ms', nombre, 'ACIERTO' if acierto else 'FALLO', ms)


def roles_activos():
    c = ROLES_PERMISOS
    if c == {'activa': False, 'backend': None}:
        c = secreto('roles_permisos')
    if c is None:
        return False
    if not hasattr(c, 'get') or type(c.get('activa', False)) is not bool:
        raise ErrorAcceso('configuracion_roles_invalida')
    return c.get('activa', False)


def _backend_roles():
    c = ROLES_PERMISOS
    if c == {'activa': False, 'backend': None}:
        c = secreto('roles_permisos')
    if c.get('backend') is not None:
        return c['backend']
    from permisos import PostgreSQL
    def conectar():
        if os.environ.get('ROBOT_FCI_SIN_RED') == '1':
            raise ErrorAcceso('conexion_deshabilitada')
        url = secreto('APP_DB_URL')
        if not url:
            raise ErrorAcceso('conexion_roles_ausente')
        import psycopg
        return psycopg.connect(url, connect_timeout=20)
    return PostgreSQL(conectar)


def rol():
    """Rol revalidado en registro servidor cuando U91 está activo."""
    return _validar_sesion()['rol']


def permitido(accion, dueno_del_dato=None):
    """Fachada Auth; el contrato puro de cinco argumentos vive en permisos.py."""
    if not roles_activos():
        return False
    from permisos import permitido as evaluar
    try:
        persona = _validar_sesion()
        if persona['cambio_obligatorio'] or not persona['version_clave']:
            return False
        papel = persona['rol']
        if accion == 'gestionar_organizaciones' and persona.get('superadmin'):
            papel = 'superadmin'
        return evaluar(papel, accion, dueno_del_dato,
                       persona['id'], persona['organizacion'])
    except ErrorAcceso:
        return False


def panel_superadmin():
    """Apagado o no autorizado: ninguna lectura de gestión."""
    if not permitido('gestionar_organizaciones'):
        return None
    from permisos import validar_panel
    try:
        return validar_panel(_backend_roles().panel(usuario()['id']))
    except Exception:
        raise ErrorAcceso('panel_no_disponible') from None


def preferencias_activas():
    c = PREFERENCIAS_USUARIO
    if c == {"activa": False, "backend": None}:
        c = secreto('preferencias_usuario')
    if c is None:
        return False
    if not isinstance(c, dict) and not hasattr(c, 'get'):
        raise ErrorAcceso('configuracion_preferencias_invalida')
    if type(c.get('activa', False)) is not bool:
        raise ErrorAcceso('configuracion_preferencias_invalida')
    return c.get('activa', False)


def lector_preferencias():
    """Backend y contexto Auth revalidado; apagado no crea backend ni lee datos."""
    if not preferencias_activas():
        return None
    try:
        anterior = dict(usuario() or {})
        persona = _validar_sesion()
        if (persona['id'] != anterior.get('id')
                or persona['organizacion'] != anterior.get('organizacion')
                or persona['rol'] not in ('admin', 'asesor')
                or persona['cambio_obligatorio'] or not persona['version_clave']):
            raise ValueError()
        contexto = {'organizacion': persona['organizacion'],
                    'usuario': str(UUID(persona['id']))}
        from preferencias_usuario import plan_guardado, PostgreSQL
        plan_guardado(contexto, {})
    except Exception:
        st.session_state.pop('preferencias_usuario_cache', None)
        raise ErrorAcceso('contexto_preferencias_rechazado') from None
    c = PREFERENCIAS_USUARIO
    if c == {"activa": False, "backend": None}:
        c = secreto('preferencias_usuario')
    backend = c.get('backend')
    if backend is None:
        def conectar():
            if os.environ.get('ROBOT_FCI_SIN_RED') == '1':
                raise ErrorAcceso('conexion_deshabilitada')
            url = secreto('APP_DB_URL')
            if not url:
                raise ErrorAcceso('conexion_preferencias_ausente')
            import psycopg
            return psycopg.connect(url, connect_timeout=20)
        backend = PostgreSQL(conectar)
    return backend, contexto


def lector_seguimiento():
    """Backend del servidor y contexto revalidado; conexión diferida por operación.

    Apagado conserva los lectores anteriores; activo incompleto rechaza.
    El registro es organización -> dueño colectivo, nunca un valor de pantalla.
    """
    configuracion = configuracion_seguimiento()
    backend = configuracion.get("backend")
    if configuracion.get("activa") is not True:
        if roles_activos():
            raise ErrorAcceso('roles_requieren_seguimiento_privado')
        return None
    registro = dict(configuracion.get("registro") or {})
    return backend, lambda: requerir_contexto_seguimiento(registro)


def configuracion_seguimiento():
    """Puente servidor; configuración activa inválida nunca recupera el legado."""
    import calculos
    motivo = 'LECTURA_CONFIGURACION'
    try:
        c = LECTURA_SEGUIMIENTO
        if c == {"activa": False, "backend": None, "registro": {}}:
            c = secreto('seguimiento_privado')
            if c is None:
                return LECTURA_SEGUIMIENTO
        if c.get('activa', False) is False:
            return LECTURA_SEGUIMIENTO if c is not LECTURA_SEGUIMIENTO else c
        motivo = 'ACTIVA'
        if c.get('activa') is not True:
            raise ValueError()
        if c.get('backend') is not None:
            motivo = 'BACKEND_REGISTRO'
            registro = dict(c['registro'])
            if not callable(c['backend'].leer):
                raise ValueError()
        else:
            motivo = 'DEPENDENCIAS'
            import psycopg
            from datos import RepositorioSeguimiento
            c = dict(c)
            motivo = 'ORGANIZACION_DUENO'
            if c.get('organizacion') != 'propia' or c.get('dueno') != 'equipo':
                raise ValueError()
            for k in ('host', 'dbname', 'user', 'password'):
                motivo = 'CAMPO_' + k.upper()
                if not isinstance(c.get(k), str) or not c[k].strip() or 'REEMPLAZAR' in c[k]:
                    raise ValueError()
            for campo, valido in (
                ('USER', re.fullmatch(r'seguimiento_app_login(?:\.[A-Za-z0-9_-]+)?', c['user'])),
                ('SSLMODE', c.get('sslmode') == 'require'),
                ('PORT', type(c.get('port')) is int and 1 <= c['port'] <= 65535),
                ('CONNECT_TIMEOUT', type(c.get('connect_timeout')) is int and 1 <= c['connect_timeout'] <= 30),
            ):
                motivo = 'CAMPO_' + campo
                if not valido:
                    raise ValueError()
            opciones = {k: c[k] for k in ('host','port','dbname','user','password','sslmode','connect_timeout')}
            registro = {'propia': 'equipo'}
            def conectar():
                if os.environ.get('ROBOT_FCI_SIN_RED') == '1':
                    raise calculos.ErrorSeguimiento('CONEXION_DESHABILITADA')
                from datos import conexion_compartida
                return conexion_compartida(**opciones)
            c['backend'] = RepositorioSeguimiento(registro, conectar)
            c['backend'].clave_cache = hash(tuple(sorted(opciones.items())))
            c['registro'] = registro
        motivo = 'REGISTRO'
        if any(not isinstance(k, str) or not k.strip()
                               or not isinstance(v, str) or not v.strip() for k, v in registro.items()):
            raise ValueError()
        return c
    except Exception as error:
        calculos.registrar_fallo_seguimiento('CONFIGURACION', motivo, error)
        raise calculos.ErrorSeguimiento('Configuración de seguimiento privado inválida o incompleta.') from None


def formulario_seguimiento(codigo, renovar=False):
    """Versiones del formulario mostrado, estables entre render y envío."""
    import calculos
    lector = lector_seguimiento()
    if lector is None:
        return None
    backend, resolver = lector
    contexto = calculos._contexto_seguimiento(resolver)
    identidad = tuple(contexto[k] for k in ('organizacion', 'dueno', 'usuario', 'rol', 'version_clave'))
    formularios = st.session_state.setdefault('seguimiento_privado_formularios', {})
    clave = (identidad, int(codigo))
    if renovar:
        formularios.pop(clave, None)
    if clave not in formularios:
        formularios[clave] = calculos.versiones_formulario_seguimiento(
            int(codigo), backend, lambda: contexto)
        formularios[clave]['clave'] = str(uuid4())
    return dict(formularios[clave])


def escribir_seguimiento(catalogo, formulario):
    """Única salida privada de escritura; nunca continúa por el legado al fallar."""
    import calculos
    try:
        revalidar_escritura()
    except ErrorAcceso:
        raise calculos.ErrorSeguimiento('CONTEXTO_RECHAZADO') from None
    lector = lector_seguimiento()
    if lector is None:
        raise calculos.ErrorSeguimiento('BACKEND_AUSENTE')
    backend, resolver = lector
    pedido = calculos.solicitar_seguimiento(catalogo, backend, formulario, resolver)
    invalidar_lecturas_privadas()
    # Solo después del commit confirmado. La caché común de Streamlit no se toca.
    for clave in list(st.session_state):
        if clave.startswith('seguimiento_privado_'):
            del st.session_state[clave]
    return pedido


def listar_seguimiento():
    import calculos
    lector = lector_seguimiento()
    if lector is None:
        raise calculos.ErrorSeguimiento('BACKEND_AUSENTE')
    pedidos = calculos.pedidos_seguimiento(*lector)
    if roles_activos():
        return [p for p in pedidos if permitido('ver_pedidos_organizacion', p)
                or permitido('ver_pedidos_propios', p)]
    return pedidos


class ErrorAcceso(Exception):
    """Mensaje fijo y seguro: nunca incluye respuestas ni secretos de Auth."""


def secreto(nombre):
    try:
        valor = st.secrets[nombre]
    except Exception:
        valor = None
    return valor or os.environ.get(nombre)


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
    papel = meta.get('rol')
    plataforma = False
    if roles_activos():
        try:
            backend = _backend_roles()
            papel = backend.rol(identificador, meta.get('organizacion'))
            plataforma = backend.rol(identificador, None) == 'superadmin'
        except Exception:
            raise ErrorAcceso('Usuario sin acceso autorizado.') from None
        aceptados = ('superadmin', 'admin', 'asesor', 'cliente')
    else:
        aceptados = ('admin', 'asesor')
    if not meta.get("organizacion") or papel not in aceptados:
        raise ErrorAcceso("Usuario sin acceso autorizado.")
    return {"id": identificador, "correo": usuario.get("email", ""),
            "organizacion": meta["organizacion"], "rol": papel,
            "cambio_obligatorio": bool(meta.get("cambio_obligatorio", True)),
            "version_clave": meta.get("version_clave"), "superadmin": plataforma}


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
    # Auth acaba de validar este usuario: la primera pantalla no repite la llamada (vale 5 minutos).
    st.session_state["auth"]["_validacion"] = ((sesion["access_token"], dict(persona), roles_activos()),
                                               dict(persona), time.monotonic())
    return persona


def usuario():
    sesion = st.session_state.get("auth")
    return sesion["persona"] if sesion else None


def revalidar_escritura():
    """Descarta la validación reutilizable antes de cada operación de escritura."""
    sesion = st.session_state.get('auth')
    anterior = dict(sesion.get('persona', {})) if sesion else {}
    if sesion:
        sesion.pop('_validacion', None)
    persona = _validar_sesion()
    if anterior and any(persona.get(k) != anterior.get(k) for k in
                        ('id', 'organizacion', 'rol', 'version_clave', 'cambio_obligatorio', 'superadmin')):
        raise ErrorAcceso('La autorización cambió. Revisá la sesión antes de guardar.')
    return persona


def _validar_sesion(forzar=False):
    from calculos import medir_seguimiento
    estado = _estado_rerun()
    if estado is None:
        return _validar_sesion_real()
    sesion = st.session_state.get('auth')
    llave = (sesion.get('access_token'), dict(sesion.get('persona', {})), roles_activos()) if sesion else None
    anterior = sesion.get('_validacion') if sesion else None
    if (not forzar and anterior is not None and anterior[0] == llave
            and time.monotonic() - anterior[2] < 300
            and time.time() < sesion['vence'] - 60):
        registrar_cache('AUTH', True)
        return dict(anterior[1])
    registrar_cache('AUTH', False)
    with medir_seguimiento('AUTH'):
        persona = _validar_sesion_real()
    if estado is not None:
        sesion = st.session_state.get('auth')
        if sesion:
            sesion['_validacion'] = ((sesion.get('access_token'), dict(persona), roles_activos()),
                                     dict(persona), time.monotonic())
    return persona


def _validar_sesion_real():
    sesion = st.session_state.get("auth")
    if not sesion:
        raise ErrorAcceso("Ingresá con tu usuario.")
    try:
        if time.time() >= sesion["vence"] - 60:
            nueva = _api("POST", "token?grant_type=refresh_token", {"refresh_token": sesion["refresh_token"]})
            sesion.update(access_token=nueva["access_token"], refresh_token=nueva["refresh_token"],
                          vence=time.time() + nueva.get("expires_in", 3600))
        persona = _identidad(_api("GET", "user", token=sesion["access_token"]))
    except (ErrorAcceso, KeyError, TypeError, ValueError, AttributeError):
        # Un token rechazado no puede conservar informes o selecciones de su dueño.
        st.session_state.clear()
        raise ErrorAcceso("La sesión ya no está autorizada. Volvé a ingresar.") from None
    if persona["version_clave"] != sesion["persona"]["version_clave"]:
        cerrar_sesion()
        raise ErrorAcceso("Tu clave fue restablecida. Volvé a ingresar.")
    campos = ("id", "organizacion", "rol", "cambio_obligatorio", "superadmin")
    if any(persona.get(k) != sesion["persona"].get(k) for k in campos):
        # Conservar únicamente la sesión revalidada; ningún resultado del contexto anterior.
        st.session_state.clear()
        st.session_state["auth"] = sesion
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


def _admin(forzar=False):
    persona = revalidar_escritura() if forzar else _validar_sesion()
    if persona["rol"] != "admin" or persona["cambio_obligatorio"]:
        raise ErrorAcceso("Solo el administrador puede gestionar usuarios.")
    return persona


def requerir_admin_clasificacion():
    """Contexto colectivo confiable; nunca recibe identidad desde un formulario.

    Solo propia/equipo está configurada en servidor en esta unidad sin base nueva.
    Conserva los controles y comportamiento del administrador de usuarios.
    """
    mensaje = "Solo un administrador autorizado puede editar la clasificación."
    try:
        revalidar_escritura()
        if roles_activos():
            _admin()
            c = configuracion_seguimiento()
            return requerir_contexto_seguimiento(c.get('registro'))
        anterior = dict(usuario() or {})
        persona = _admin()
        if (persona["id"] != anterior.get("id")
                or persona["organizacion"] != anterior.get("organizacion")
                or not persona.get("version_clave")
                or persona["organizacion"] != "propia"):
            raise ErrorAcceso(mensaje)
        return {"usuario": persona["id"], "organizacion": "propia", "dueno": "equipo",
                "rol": "admin", "version_clave": persona["version_clave"]}
    except (ErrorAcceso, KeyError, TypeError, ValueError, AttributeError):
        raise ErrorAcceso(mensaje) from None


def requerir_contexto_seguimiento(registro=None):
    """Registro servidor organización -> dueño colectivo; sin configuración rechaza.

    No está conectado a pantallas. El registro inyectado nunca viene del formulario.
    Cada lectura/escritura debe llamar de nuevo a este control.
    """
    mensaje = "Contexto de seguimiento no autorizado."
    motivo = 'SESION'
    try:
        anterior = dict(usuario() or {})
        persona = _validar_sesion()
        organizacion = persona["organizacion"]
        dueno = registro.get(organizacion) if isinstance(registro, dict) else None
        for motivo, valido in (
            ('ORGANIZACION', isinstance(organizacion, str) and bool(organizacion.strip())),
            ('REGISTRO_DUENO', isinstance(dueno, str) and bool(dueno.strip())),
            ('IDENTIDAD_CAMBIO', persona['id'] == anterior.get('id')),
            ('ORGANIZACION_CAMBIO', organizacion == anterior.get('organizacion')),
            ('ROL', persona['rol'] in (('admin', 'asesor', 'cliente') if roles_activos()
                                      else ('admin', 'asesor'))),
            ('CAMBIO_OBLIGATORIO', not persona['cambio_obligatorio']),
            ('VERSION_CLAVE', bool(persona['version_clave'])),
        ):
            if not valido:
                raise ErrorAcceso(mensaje)
        motivo = 'USUARIO_UUID'
        identificador = str(UUID(persona["id"]))
        contexto = {"organizacion": organizacion, "dueno": dueno, "usuario": identificador,
                    "rol": persona["rol"], "version_clave": persona["version_clave"]}
        if roles_activos():
            contexto['roles_permisos'] = True
        return contexto
    except (ErrorAcceso, KeyError, TypeError, ValueError, AttributeError) as error:
        from calculos import registrar_fallo_seguimiento
        registrar_fallo_seguimiento('AUTH', motivo, error)
        raise ErrorAcceso(mensaje) from None


def validar_correo(correo, confirmacion):
    correo = correo.strip()
    if correo.casefold() != confirmacion.strip().casefold():
        raise ErrorAcceso("Los correos no coinciden.")
    partes = correo.rsplit("@", 1)
    if len(partes) != 2 or len(correo) > 254:
        raise ErrorAcceso("Ingresá un correo válido.")
    local, dominio = partes
    etiquetas = dominio.split(".")
    if (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or len(etiquetas) < 2 or not re.fullmatch(r"[A-Za-z]{2,63}", etiquetas[-1])
            or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", e) for e in etiquetas)):
        raise ErrorAcceso("Ingresá un correo válido.")
    return local + "@" + dominio.lower()


def aviso_dominio(correo):
    dominio = correo.rsplit("@", 1)[-1].lower()
    errores = {"gmal.com": "gmail.com", "gmial.com": "gmail.com", "gamil.com": "gmail.com",
               "gnail.com": "gmail.com", "gmail.con": "gmail.com", "gmail.co": "gmail.com",
               "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com",
               "hotamil.com": "hotmail.com", "outlok.com": "outlook.com", "yaho.com": "yahoo.com"}
    sugerido = errores.get(dominio)
    return f"El dominio {dominio} parece mal escrito. ¿Quisiste poner {sugerido}? Revisalo antes de confirmar." if sugerido else ""


def crear_usuario(correo, confirmacion=None, admin=False, inicial=False):
    # inicial es exclusivamente para el comando local del operador del servidor.
    organizacion = "propia" if inicial else _admin(forzar=True)["organizacion"]
    correo = validar_correo(correo, correo if inicial else (confirmacion or ""))
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


def restablecer_clave(identificador, clave, confirmacion):
    persona = _admin(forzar=True)
    if len(clave) < 12 or clave != confirmacion:
        raise ErrorAcceso("Usá al menos 12 caracteres y repetí la misma clave.")
    identificador = str(UUID(identificador))
    destino = _api("GET", "admin/users/" + identificador, admin=True)
    meta = destino.get("app_metadata", {})
    if meta.get("organizacion") != persona["organizacion"]:
        raise ErrorAcceso("El usuario no pertenece a tu organización.")
    _api("PUT", "admin/users/" + identificador,
         {"password": clave, "app_metadata": {**meta, "cambio_obligatorio": True,
                                                   "version_clave": str(uuid4())}}, admin=True)


def cambiar_clave(clave, confirmacion):
    persona = revalidar_escritura()
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
    st.session_state['auth'].pop('_validacion', None)
    invalidar_lecturas_privadas()


def _pantalla_usuarios():
    _admin()
    with st.sidebar.expander("Usuarios"):
        try:
            with st.form("crear_usuario", clear_on_submit=True):
                correo = st.text_input("Correo del nuevo usuario")
                repetir = st.text_input("Repetí el correo")
                admin = st.checkbox("Administrador de la organización")
                revisar = st.form_submit_button("Revisar alta")
            if revisar:
                st.session_state.pop("alta_pendiente", None)
                correo = validar_correo(correo, repetir)
                st.session_state["alta_pendiente"] = (correo, admin)
            pendiente = st.session_state.get("alta_pendiente")
            if pendiente:
                correo, admin = pendiente
                st.write("Correo final: " + correo)
                st.write("Rol: " + ("Administrador" if admin else "Asesor"))
                aviso = aviso_dominio(correo)
                if aviso:
                    st.warning(aviso)
                if st.button("Confirmar alta"):
                    st.session_state.pop("alta_pendiente", None)
                    temporal = crear_usuario(correo, correo, admin=admin)
                    st.caption("Clave temporal: pasala a la persona. Se muestra una sola vez.")
                    st.code(temporal, language=None)
                if st.button("Cancelar alta"):
                    st.session_state.pop("alta_pendiente", None)
                    st.rerun()
            if st.button("Ver usuarios"):
                st.session_state["mostrar_usuarios"] = True
            if st.session_state.get("mostrar_usuarios"):
                personas = listar_usuarios()
                if personas:
                    opciones = {p["id"]: p["correo"] + " · " + p["rol"] for p in personas}
                    with st.form("reasignar_clave", clear_on_submit=True):
                        destino = st.selectbox("Usuario", list(opciones), format_func=opciones.get)
                        clave = st.text_input("Nueva clave del usuario (12 caracteres o más)", type="password")
                        repetir = st.text_input("Repetí la clave del usuario", type="password")
                        reasignar = st.form_submit_button("Reasignar clave")
                    if reasignar:
                        restablecer_clave(destino, clave, repetir)
                        st.success("Clave reasignada. El usuario deberá cambiarla al ingresar.")
        except ErrorAcceso as error:
            st.error(str(error))


def requerir(accion='consultar'):
    """Único control de todas las pantallas; exige cambiar la clave antes de mostrar datos."""
    import marca
    import calculos
    try:
        configuracion_seguimiento()
    except calculos.ErrorSeguimiento as error:
        st.error(str(error))
        st.stop()
    if (not str(secreto("SUPABASE_URL") or "").startswith("https://")
            or not secreto("SUPABASE_ANON_KEY") or not secreto("SUPABASE_SERVICE_KEY")):
        st.error("Falta configurar el acceso de usuarios: SUPABASE_URL, SUPABASE_ANON_KEY y SUPABASE_SERVICE_KEY. Consultá al administrador.")
        st.stop()
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
        if roles_activos():
            from permisos import permitido as evaluar
            dato = {'organizacion': persona['organizacion'], 'dueno': persona['id']}
            papel = persona['rol']
            if accion == 'gestionar_organizaciones' and persona.get('superadmin'):
                papel = 'superadmin'
            if not evaluar(papel, accion,
                           None if accion == 'gestionar_organizaciones' else dato,
                           persona['id'], persona['organizacion']):
                st.info(marca.textos_panel()['aviso'])
                st.stop()
        try:
            import datos
            st.session_state["marca"] = datos.organizacion(persona)
        except datos.ErrorPreferencias:
            st.error("No se pudo cargar la organización y sus preferencias.")
            st.stop()
        marca.mostrar()
        if persona["rol"] == "admin":
            _pantalla_usuarios()
        # El marco de navegación identifica las corridas de la app completa.
        # Los consumidores puros de acceso no deben disparar consultas de pantallas.
        if (_estado_rerun() or {}).get('pagina') is not None and persona['rol'] in ('admin', 'asesor', 'cliente'):
            import rapidez
            rapidez.precalentar_sesion(persona)
        return
    st.title(marca.nombre())
    st.caption("Acceso restringido.")
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
    with st.expander("Olvidé mi clave"):
        st.write("Pedile a tu administrador que restablezca tu clave.")
    st.stop()
