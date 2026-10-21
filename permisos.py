"""U91: contrato cerrado, independiente de Streamlit y de conexiones."""
ROLES = ('superadmin', 'admin', 'asesor', 'cliente')
ACCIONES = ('consultar', 'pedir_seguir', 'pedir_dejar', 'editar_texto_propio',
            'editar_texto_ajeno', 'ver_pedidos_propios', 'ver_pedidos_organizacion',
            'clasificar_instrumento', 'gestionar_usuarios_organizacion',
            'gestionar_organizaciones')
MATRIZ = {
    'superadmin': frozenset(('gestionar_organizaciones',)),
    'admin': frozenset(ACCIONES) - {'gestionar_organizaciones'},
    'asesor': frozenset(('consultar', 'pedir_seguir', 'pedir_dejar',
                         'editar_texto_propio', 'ver_pedidos_propios')),
    'cliente': frozenset(('consultar',)),
}


def permitido(rol, accion, dueno_del_dato, usuario, organizacion):
    """Dueño: {organizacion, dueno, usuario}; usuario es el autor del pedido de seguir.

    No inferir autor desde el último editor. Gestión de plataforma recibe None
    como dato y organización explícita de la sesión; nunca concede datos privados.
    """
    if (not isinstance(rol, str) or not isinstance(accion, str)
            or accion not in MATRIZ.get(rol, ())
            or not isinstance(usuario, str) or not usuario.strip()
            or not isinstance(organizacion, str) or not organizacion.strip()):
        return False
    if rol == 'superadmin':
        return dueno_del_dato is None
    if (not isinstance(dueno_del_dato, dict)
            or dueno_del_dato.get('organizacion') != organizacion
            or not isinstance(dueno_del_dato.get('dueno'), str)
            or not dueno_del_dato['dueno'].strip()):
        return False
    if accion in ('editar_texto_propio', 'ver_pedidos_propios'):
        return dueno_del_dato.get('usuario') == usuario
    if accion == 'editar_texto_ajeno':
        return bool(dueno_del_dato.get('usuario')) and dueno_del_dato['usuario'] != usuario
    return True


def validar_panel(datos):
    """Proyección permitida: rechaza columnas extra en lugar de mostrarlas."""
    if not isinstance(datos, dict) or set(datos) != {'organizaciones', 'usuarios'}:
        raise ValueError('PANEL_INVALIDO')
    organizaciones, usuarios = datos['organizaciones'], datos['usuarios']
    if not isinstance(organizaciones, list) or not isinstance(usuarios, list):
        raise ValueError('PANEL_INVALIDO')
    ids = set()
    for o in organizaciones:
        if (not isinstance(o, dict) or set(o) != {'id', 'nombre'}
                or any(not isinstance(v, str) or not v.strip() for v in o.values())
                or o['id'] in ids):
            raise ValueError('PANEL_INVALIDO')
        ids.add(o['id'])
    for u in usuarios:
        if (not isinstance(u, dict) or set(u) != {'organizacion', 'dueno', 'rol'}
                or (u['rol'] == 'superadmin' and u['organizacion'] is not None)
                or (u['rol'] != 'superadmin' and u['organizacion'] not in ids)
                or u['rol'] not in ROLES
                or not isinstance(u['dueno'], str) or not u['dueno'].strip()):
            raise ValueError('PANEL_INVALIDO')
    return {'organizaciones': [dict(o) for o in organizaciones],
            'usuarios': [dict(u) for u in usuarios]}


class PostgreSQL:
    """Conector servidor inyectado; funciones SQL sin escritura ni datos de fondos."""
    def __init__(self, conectar):
        self.conectar = conectar

    def rol(self, usuario, organizacion):
        with self.conectar() as c:
            return c.execute('select public.roles_resolver(%s::uuid,%s)',
                             (usuario, organizacion)).fetchone()[0]

    def panel(self, usuario):
        with self.conectar() as c:
            return c.execute('select public.roles_panel(%s::uuid)', (usuario,)).fetchone()[0]
