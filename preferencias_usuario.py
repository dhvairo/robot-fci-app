"""U87: contrato privado; contexto confiable suministrado por el servidor.

Sin acceso a pantallas ni conexiones implícitas. PostgreSQL recibe una fábrica
de conexiones DB-API nuevas, conectadas directamente con el rol de la app.
"""
import json
from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from uuid import UUID

if __package__:
    from .navegacion import SECCIONES
else:
    from navegacion import SECCIONES


class ErrorPreferencia(ValueError):
    """El mensaje contiene únicamente un motivo cerrado."""

    def __init__(self, motivo):
        self.motivo = motivo
        super().__init__(motivo)


def _fallar(motivo):
    raise ErrorPreferencia(motivo)


def _opcion(opciones):
    def comprobar(valor):
        if type(valor) is not str:
            _fallar('tipo_invalido')
        if valor not in opciones:
            _fallar('valor_invalido')
        return valor
    return comprobar


def _favoritos(valor):
    if type(valor) is not list:
        _fallar('tipo_invalido')
    if any(type(codigo) is not int for codigo in valor):
        _fallar('tipo_invalido')
    if len(valor) > 200 or any(codigo <= 0 for codigo in valor):
        _fallar('valor_invalido')
    return list(dict.fromkeys(valor))


def _booleano(valor):
    if type(valor) is not bool:
        _fallar('tipo_invalido')
    return valor


def _filas(valor):
    if type(valor) is not int:
        _fallar('tipo_invalido')
    if not 10 <= valor <= 200:
        _fallar('valor_invalido')
    return valor


# Valores predeterminados inmutables; cada resultado recibe su propia lista.
PAGINAS = tuple(titulo for seccion in SECCIONES.values() for _, titulo, _ in seccion)
CATALOGO = MappingProxyType({
    'favoritos': ((), _favoritos),
    'pantalla_inicial': (PAGINAS[0], _opcion(PAGINAS)),
    'avisos_correo': (False, _booleano),
    'filtro_seguimiento': ('seguidos', _opcion(('seguidos', 'no_seguidos', 'todos'))),
    'moneda_informe': ('todos', _opcion(('pesos', 'dolares', 'todos'))),
    'filas_por_pagina': (50, _filas),
})


def validar(clave, valor):
    if type(clave) is not str or clave not in CATALOGO:
        _fallar('clave_desconocida')
    return CATALOGO[clave][1](valor)


def efectivas(guardadas):
    """Devuelve {valores, descartadas}; los valores conocidos inválidos fallan."""
    if not isinstance(guardadas, Mapping):
        _fallar('tipo_invalido')
    valores = {k: ([] if k == 'favoritos' else deepcopy(v[0])) for k, v in CATALOGO.items()}
    descartadas = 0
    for clave, valor in guardadas.items():
        if clave not in CATALOGO:
            descartadas += 1
        else:
            valores[clave] = validar(clave, valor)
    return dict(valores=valores, descartadas=descartadas)


def _identidad(contexto):
    if not isinstance(contexto, Mapping):
        _fallar('tipo_invalido')
    organizacion, usuario = contexto.get('organizacion'), contexto.get('usuario')
    if type(organizacion) is not str or type(usuario) is not str:
        _fallar('tipo_invalido')
    if not organizacion or organizacion != organizacion.strip():
        _fallar('valor_invalido')
    try:
        usuario = str(UUID(usuario))
    except ValueError:
        _fallar('valor_invalido')
    return organizacion, usuario


def plan_guardado(contexto, cambios):
    organizacion, usuario = _identidad(contexto)
    if not isinstance(cambios, Mapping):
        _fallar('tipo_invalido')
    return [dict(organizacion=organizacion, dueno=usuario, clave=k, valor=validar(k, v))
            for k, v in cambios.items()]


class Preferencias:
    """Adaptador mínimo; solo admite cambios del catálogo, nunca identidad del formulario."""

    def __init__(self, backend):
        self.backend = backend

    def leer(self, contexto):
        return efectivas(self.backend.leer(_identidad(contexto)))

    def guardar(self, contexto, cambios):
        filas = plan_guardado(contexto, cambios)
        if filas:
            self.backend.guardar(_identidad(contexto), filas)
        return filas


class Memoria:
    def __init__(self):
        self._filas = {}

    def leer(self, identidad):
        return {k: deepcopy(v) for (o, u, k), v in self._filas.items() if (o, u) == identidad}

    def guardar(self, identidad, filas):
        if any((f['organizacion'], f['dueno']) != identidad for f in filas):
            _fallar('valor_invalido')
        nuevas = {(f['organizacion'], f['dueno'], f['clave']): deepcopy(f['valor']) for f in filas}
        self._filas.update(nuevas)


class PostgreSQL:
    def __init__(self, conectar):
        self.conectar = conectar

    def _ejecutar(self, identidad, operacion):
        conexion = self.conectar()
        try:
            # BEGIN explícito sirve también con conexiones autocommit.
            with conexion.cursor() as cursor:
                cursor.execute('BEGIN')
                cursor.execute('select set_config(%s,%s,true), set_config(%s,%s,true)',
                               ('app.organizacion', identidad[0], 'app.dueno', identidad[1]))
                resultado = operacion(cursor)
            conexion.commit()
            return resultado
        except Exception:
            conexion.rollback()
            raise
        finally:
            conexion.close()

    def leer(self, identidad):
        def operacion(cursor):
            cursor.execute('select clave, valor from public.preferencias '
                           'where organizacion=%s and dueno=%s', identidad)
            return dict(cursor.fetchall())
        return self._ejecutar(identidad, operacion)

    def guardar(self, identidad, filas):
        def operacion(cursor):
            for fila in filas:
                cursor.execute('insert into public.preferencias (organizacion,dueno,clave,valor) '
                               'values (%s,%s,%s,%s::jsonb) on conflict (organizacion,dueno,clave) '
                               'do update set valor=excluded.valor, actualizada=now() '
                               'where public.preferencias.valor is distinct from excluded.valor',
                               (fila['organizacion'], fila['dueno'], fila['clave'], json.dumps(fila['valor'])))
        self._ejecutar(identidad, operacion)


# Repo y copia plana comparten tipos, incluso al mezclar imports en las pruebas.
import sys
sys.modules.setdefault('preferencias_usuario', sys.modules[__name__])
sys.modules.setdefault('app.preferencias_usuario', sys.modules[__name__])
