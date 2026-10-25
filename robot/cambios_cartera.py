"""U125: comparaciones reproducibles de composiciones públicas, sin servicios ni dependencias.

Se distribuye también con la app. La clasificación es la guardada con cada cartera;
una diferencia descriptiva no demuestra compras, ventas ni decisiones de gestión.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import unicodedata


SQL_HISTORIA = """select h.codigo_cnv, h.fecha_cartera,
coalesce(to_jsonb(h)->>'fuente', 'cnv') as fuente,
c.orden, c.instrumento, c.pct_pn, c.rubro_cnv, c.categoria_resumen,
c.categoria_detallada, c.moneda, c.indexacion,
to_jsonb(c)->>'especie' as especie, to_jsonb(c)->>'ticker' as ticker,
to_jsonb(c)->>'codigo_activo' as codigo_activo,
to_jsonb(c)->>'serie' as serie, to_jsonb(c)->>'vencimiento' as vencimiento
from carteras_fechas h left join carteras c using (codigo_cnv, fecha_cartera)
"""
CAMPOS = ('rubro_cnv', 'categoria_resumen', 'categoria_detallada', 'moneda', 'indexacion')
RESTOS = {'resto', 'resto de activos', 'resto de cartera', 'sin detalle'}
PARCIALES = {'cafci', 'ficha unica', 'ficha única', 'ficha_unica'}


def normalizar(valor):
    # Conserva acentos, puntuación y números: no interpreta tickers dentro de nombres.
    return ' '.join(unicodedata.normalize('NFC', str(valor or '')).split()).casefold()


def es_resto(fila):
    return (normalizar(fila.get('instrumento')) in RESTOS
            or normalizar(fila.get('categoria_resumen')) == 'sin detalle')


def identidad(fila):
    codigos = tuple((k, normalizar(fila[k])) for k in
                    ('codigo_activo', 'codigo_especie', 'codigo', 'isin', 'especie', 'ticker')
                    if fila.get(k) is not None and str(fila[k]).strip())
    extra = tuple(normalizar(fila.get(k)) for k in ('serie', 'vencimiento'))
    if codigos:
        return ('codigo', codigos, extra)
    nombre = normalizar(fila.get('instrumento'))
    if not nombre:
        raise ValueError('Activo sin nombre ni código: comparación no disponible.')
    return ('nombre', nombre, normalizar(fila.get('rubro_cnv')), extra)


def _peso(fila):
    try:
        valor = Decimal(str(fila.get('pct_pn')))
    except (InvalidOperation, ValueError):
        raise ValueError('Hay pesos sin informar: comparación no disponible.') from None
    if not valor.is_finite():
        raise ValueError('Hay pesos sin informar: comparación no disponible.')
    return valor


def _composicion(cartera):
    if not cartera['filas']:
        raise ValueError('Sin detalle guardado: comparación no disponible.')
    activos, tipos = {}, {k: defaultdict(Decimal) for k in CAMPOS}
    resto = Decimal(0)
    tiene_resto = False
    for f in cartera['filas']:
        peso = _peso(f)
        for campo in CAMPOS:
            tipos[campo][str(f.get(campo) or 'Sin dato')] += peso
        if es_resto(f):
            resto += peso
            tiene_resto = True
            continue
        clave = identidad(f)
        if clave in activos:
            # Una repetición sin código podría esconder especies diferentes.
            if clave[0] == 'nombre':
                raise ValueError('Nombre repetido sin especie/código: identidad ambigua; comparación no disponible.')
            activos[clave]['peso'] += peso
        else:
            activos[clave] = {'instrumento': f['instrumento'], 'peso': peso}
    return activos, tipos, resto, tiene_resto


def _cambio(nombre, antes, despues, **extras):
    return dict(instrumento=nombre, antes=float(antes), despues=float(despues),
                diferencia=float(despues - antes), **extras)


def comparar(actual, anterior=None):
    fecha = date.fromisoformat(str(actual['fecha_cartera'])[:10])
    resultado = dict(fecha=fecha, anterior=None, dias=None, estado='primera cartera, sin comparación',
                     entraron=[], salieron=[], cambio_peso=[], clasificacion=[], resto=None,
                     rotacion=None, parcial=False, cambio_fuente=False, avisos=[], con_cambios=False,
                     fuente_actual=actual.get('fuente') or 'cnv', fuente_anterior=None)
    if anterior is None:
        return resultado
    previa = date.fromisoformat(str(anterior['fecha_cartera'])[:10])
    if previa >= fecha:
        raise ValueError('La cartera anterior debe tener una fecha estrictamente previa.')
    if actual.get('codigo_cnv') != anterior.get('codigo_cnv'):
        raise ValueError('Solo se comparan carteras del mismo fondo.')
    resultado.update(anterior=previa, dias=(fecha - previa).days, estado='comparada',
                     fuente_anterior=anterior.get('fuente') or 'cnv')
    resultado['cambio_fuente'] = normalizar(resultado['fuente_actual']) != normalizar(resultado['fuente_anterior'])
    if resultado['cambio_fuente']:
        resultado['avisos'].append('Cambio de fuente: las diferencias de fuente o detalle no se atribuyen a la gestión.')
    parcial = any(normalizar(c.get('fuente')) in PARCIALES or any(es_resto(f) for f in c['filas'])
                  for c in (actual, anterior))
    resultado['parcial'] = parcial
    if parcial:
        resultado['avisos'].append('Comparación parcial: los cambios dentro del Resto no se ven. '
            'Un activo que salió del detalle puede seguir en Resto; la rotación corresponde al detalle observado.')
    try:
        nuevos, tipos_n, resto_n, hay_n = _composicion(actual)
        viejos, tipos_v, resto_v, hay_v = _composicion(anterior)
    except ValueError as error:
        resultado['estado'] = str(error)
        resultado['avisos'].append(str(error))
        return resultado
    diferencias = abs(resto_n - resto_v)
    for clave in sorted(viejos.keys() | nuevos.keys(), key=repr):
        a, n = viejos.get(clave), nuevos.get(clave)
        antes, despues = a['peso'] if a else Decimal(0), n['peso'] if n else Decimal(0)
        diferencias += abs(despues - antes)
        coincidencia = 'coincidencia por nombre' if clave[0] == 'nombre' else 'especie/ticker o código'
        identificacion = ' · '.join(f'{k}: {v}' for k, v in clave[1]) if clave[0] == 'codigo' else 'nombre y rubro'
        fila = _cambio((n or a)['instrumento'], antes, despues, identidad=identificacion, coincidencia=coincidencia)
        if a is None:
            resultado['entraron'].append(dict(fila, movimiento='entró al detalle (podía estar en Resto)' if parcial else 'entró'))
        elif n is None:
            resultado['salieron'].append(dict(fila, movimiento='salió del detalle (puede seguir en Resto)' if parcial else 'salió'))
        elif antes != despues:
            resultado['cambio_peso'].append(fila)
    for campo in CAMPOS:
        for tipo in sorted(tipos_v[campo].keys() | tipos_n[campo].keys()):
            antes, despues = tipos_v[campo][tipo], tipos_n[campo][tipo]
            if antes != despues:
                resultado['clasificacion'].append(_cambio(tipo, antes, despues, campo=campo))
    if hay_n or hay_v:
        resultado['resto'] = _cambio('Resto', resto_v, resto_n)
    resultado['rotacion'] = float(diferencias / 2)
    resultado['con_cambios'] = bool(resultado['entraron'] or resultado['salieron'] or resultado['cambio_peso']
        or resultado['clasificacion'] or resto_n != resto_v or resultado['cambio_fuente'])
    for campo in ('entraron', 'salieron', 'cambio_peso', 'clasificacion'):
        resultado[campo].sort(key=lambda f: (-abs(f['diferencia']), f['instrumento'], f.get('identidad', '')))
    return resultado


def carteras_desde_filas(filas):
    grupos = {}
    for fila in filas:
        clave = (fila['codigo_cnv'], fila['fecha_cartera'])
        cartera = grupos.setdefault(clave, dict(codigo_cnv=clave[0], fecha_cartera=clave[1],
                                               fuente=fila.get('fuente') or 'cnv', filas=[]))
        if fila.get('orden') is not None:
            cartera['filas'].append(fila)
    return list(grupos.values())


def historial(carteras):
    por_fecha = {}
    for cartera in carteras:
        clave = (cartera.get('codigo_cnv'), str(cartera['fecha_cartera'])[:10])
        if clave in por_fecha and cartera != por_fecha[clave]:
            raise ValueError('Más de una cartera para el mismo fondo y fecha: seleccionar una revisión.')
        por_fecha[clave] = cartera
    anteriores, resultados = {}, []
    for _, cartera in sorted(por_fecha.items(), key=lambda par: (str(par[0][0]), par[0][1])):
        codigo = cartera.get('codigo_cnv')
        resultados.append(comparar(cartera, anteriores.get(codigo)))
        anteriores[codigo] = cartera
    return list(reversed(resultados))


def resumen(nombre, cambio):
    if cambio['rotacion'] is None:
        return f"CAMBIOS {nombre} ({cambio['fecha']:%d/%m}): {cambio['estado']}"
    extra = ('; comparación parcial' if cambio['parcial'] else '')
    if cambio['cambio_fuente']:
        extra += f"; cambio de fuente {cambio['fuente_anterior']} → {cambio['fuente_actual']} (sin atribuir a gestión)"
    if cambio['parcial']:
        extra += '; salieron = salió del detalle (puede seguir en Resto)'
    return (f"CAMBIOS {nombre} ({cambio['anterior']:%d/%m} → {cambio['fecha']:%d/%m}): "
            f"entraron {len(cambio['entraron'])}, salieron {len(cambio['salieron'])}, "
            f"cambió peso {len(cambio['cambio_peso'])}; rotación {cambio['rotacion']:.2f} %{extra}")


def comparar_guardada(conn, codigo, cartera, filas):
    """Solo la fecha inmediatamente anterior; misma transacción que el guardado."""
    from psycopg.rows import dict_row
    fecha = cartera['fecha_cartera']
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(SQL_HISTORIA + "where h.codigo_cnv = %s and h.fecha_cartera = "
                    "(select max(fecha_cartera) from carteras_fechas "
                    "where codigo_cnv = %s and fecha_cartera < %s) order by c.orden", (codigo, codigo, fecha))
        previas = carteras_desde_filas(cur.fetchall())
    actual = dict(codigo_cnv=codigo, fecha_cartera=fecha, fuente=cartera.get('fuente') or 'cnv',
        filas=[dict(f, instrumento=f['detalle'], pct_pn=f['pct'], rubro_cnv=f['rubro']) for f in filas])
    return comparar(actual, previas[0] if previas else None)
