"""Lógica de las pantallas que se puede probar sin Streamlit ni base: filtros, períodos y cabecera de la ficha."""
import unicodedata
import json
import logging
import re
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache

import pandas as pd

import formato


# U25: contratos de datos; no asignan UUID, consultan repositorios ni escriben.
CAMPOS_CLASIFICACION = ("categoria_resumen", "categoria_detallada", "moneda", "indexacion")
CAMPOS_IDENTIDAD = ("codigo_cnv", "rubro_cnv_original", "instrumento_original",
                    "moneda_del_fondo_original", "serie", "vencimiento")


class ErrorClasificacion(ValueError):
    """Código fijo, sin contenido de las entradas."""


def preferencias_usuario(sesion, *, cambios=None, restablecer=False):
    """Lectura por sesión/identidad; escritura invalida tras commit."""
    from acceso import lector_preferencias
    from preferencias_usuario import Preferencias, efectivas, ErrorPreferencia
    if cambios is not None or restablecer:
        from acceso import preferencias_activas, revalidar_escritura
        if preferencias_activas():
            revalidar_escritura()
    lector = lector_preferencias()
    if lector is None:
        sesion.pop('preferencias_usuario_cache', None)
        return None
    backend, contexto = lector
    identidad = (contexto['organizacion'], contexto['usuario'])
    repo = Preferencias(backend)
    if restablecer:
        cambios = efectivas({})['valores']
    try:
        # Validar el lote completo antes de cualquier escritura o actualización local.
        from preferencias_usuario import plan_guardado
        if cambios is not None:
            plan_guardado(contexto, cambios)
        cache = sesion.get('preferencias_usuario_cache')
        acierto = cache is not None and cache['identidad'] == identidad
        from acceso import registrar_cache
        registrar_cache('PREFERENCIAS', acierto)
        if not acierto:
            # Conservar presencia de claves: un default no es una preferencia guardada.
            cache = {'identidad': identidad, 'guardadas': backend.leer(identidad),
                     'leida': time.monotonic()}
            sesion['preferencias_usuario_cache'] = cache
            try:
                cache['resultado'] = efectivas(cache['guardadas'])
            except ErrorPreferencia as error:
                cache['error'] = error.motivo
        if 'error' in cache:
            raise ErrorPreferencia(cache['error'])
        if cambios is not None:
            repo.guardar(contexto, cambios)
            # Sesiones abiertas con U88 aún no conservan las claves explícitas.
            cache.setdefault('guardadas', {}).update(deepcopy(cambios))
            cache['resultado'] = efectivas({**cache['resultado']['valores'], **cambios})
            from acceso import invalidar_lecturas_privadas
            invalidar_lecturas_privadas()
            sesion.pop('preferencias_usuario_cache', None)
        return deepcopy(cache['resultado']['valores'])
    except ErrorPreferencia:
        raise
    except Exception:
        raise ErrorPreferencia('operacion_preferencias_fallida') from None


def resolver_filtro_seguimiento(activa, identidad, preferencias, defecto, elegido=None):
    """Resolución pura: elección de sesión, preferencia explícita o default previo."""
    etiquetas = {'seguidos': 'Seguidos', 'no_seguidos': 'No seguidos', 'todos': 'Todos'}
    if elegido in etiquetas.values():
        return elegido
    if activa is True and identidad and isinstance(preferencias, dict):
        valor = preferencias.get('filtro_seguimiento')
        if isinstance(valor, str) and valor in etiquetas:
            return etiquetas[valor]
    return defecto


def filtro_seguimiento_inicial(sesion):
    """Reutiliza la lectura U88; sin identidad/preferencia válida conserva cada pantalla."""
    from acceso import ErrorAcceso
    from preferencias_usuario import ErrorPreferencia
    try:
        valores = preferencias_usuario(sesion)
    except (ErrorAcceso, ErrorPreferencia):
        return None
    if valores is None:
        return None
    cache = sesion['preferencias_usuario_cache']
    return resolver_filtro_seguimiento(True, cache['identidad'], cache.get('guardadas'), None)


def _normalizar_componente(valor):
    if valor is None:
        return None
    if not isinstance(valor, (str, int)) or isinstance(valor, bool):
        raise ErrorClasificacion("IDENTIDAD_INVALIDA")
    return " ".join(unicodedata.normalize("NFC", str(valor)).split())


def _componentes(fila):
    valores = tuple(_normalizar_componente(fila.get(k)) for k in CAMPOS_IDENTIDAD)
    if any(not v for v in valores[:4]) or any(v == "" for v in valores[4:]):
        raise ErrorClasificacion("IDENTIDAD_INVALIDA")
    return ("CNV", *valores)


def identidad_instrumento(fila, contexto):
    """contexto.apariciones: snapshot completo, una fecha/revisión por fondo.

    Devuelve componentes, clave JSON completa y estado; hash nunca es identidad.
    Serie/vencimiento opcionales complementan el nombre original sin inferencias.
    """
    componentes = _componentes(fila)
    apariciones = contexto.get("apariciones")
    if not isinstance(apariciones, list):
        raise ErrorClasificacion("CONTEXTO_IDENTIDAD_INVALIDO")
    cantidad = sum(_componentes(f) == componentes for f in apariciones)
    if not cantidad:
        raise ErrorClasificacion("CONTEXTO_IDENTIDAD_INVALIDO")
    return {"componentes": componentes,
            "clave": json.dumps(componentes, ensure_ascii=False, separators=(",", ":")),
            "estado": "ambiguo" if cantidad > 1 else "inequivoco"}


def _tupla_clasificacion(fuente):
    if not fuente or fuente.get("sin_clasificar"):
        return None
    valores = tuple(fuente.get(k) for k in CAMPOS_CLASIFICACION)
    if any(not isinstance(v, str) or not v.strip() for v in valores[:2]):
        return None
    if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in valores[2:]):
        return None
    return valores


def _contexto_clasificacion(contexto):
    if (not isinstance(contexto, dict)
            or any(not isinstance(contexto.get(k), str) or not contexto[k].strip()
                   for k in ("organizacion", "dueno"))):
        raise ErrorClasificacion("CONTEXTO_INVALIDO")
    return contexto["organizacion"], contexto["dueno"]


def _reglas_propias(reglas, contexto):
    org, dueno = _contexto_clasificacion(contexto)
    return [r for r in reglas if r.get("activa") is True
            and r.get("organizacion") == org and r.get("dueno") == dueno]


def _aplicar_parche(fuente, parche):
    if not isinstance(parche, dict) or set(parche) - set(CAMPOS_CLASIFICACION):
        raise ErrorClasificacion("PARCHE_INVALIDO")
    if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in parche.values()):
        raise ErrorClasificacion("PARCHE_INVALIDO")
    final = {k: deepcopy(fuente.get(k)) for k in CAMPOS_CLASIFICACION}
    final.update(deepcopy(parche))
    return final


def _identidades_lectura(filas):
    """Normaliza cada objeto una vez; serializa una vez cada identidad completa."""
    identidades, serializadas = {}, {}
    for fila in filas:
        if id(fila) in identidades:
            continue
        try:
            # Una fuente de la base no trae codigo_cnv: evita la excepción en el caso corriente.
            clave = _componentes(fila) if fila.get('codigo_cnv') is not None else None
        except ErrorClasificacion:
            clave = None
        if clave not in serializadas:
            serializadas[clave] = json.dumps(clave, ensure_ascii=False, separators=(',', ':'))
        identidades[id(fila)] = (clave, serializadas[clave])
    return identidades


def _opciones_indexadas(filas, propias, identidades, tuplas=None):
    """tuplas (opcional) recibe la tupla de cada objeto fila, para no recalcularla."""
    opciones, por_instrumento = set(), {}
    for fila in filas:
        tupla = _tupla_clasificacion(fila)
        if tuplas is not None:
            tuplas[id(fila)] = tupla
        if tupla:
            opciones.add(tupla)
        if propias:
            clave, serializada = identidades[id(fila)]
            if clave is not None:
                por_instrumento.setdefault(serializada, []).append(fila)
    for regla in propias:
        tupla = _tupla_clasificacion(_aplicar_parche(regla.get('fuente_base') or {}, regla['parche']))
        if tupla:
            opciones.add(tupla)
        for fila in por_instrumento.get(regla['instrumento'], ()):
            vigente = _tupla_clasificacion(_aplicar_parche(fila, regla['parche']))
            if vigente:
                opciones.add(vigente)
    return tuple(sorted(opciones, key=lambda t: json.dumps(t, ensure_ascii=False)))


def opciones_clasificacion(filas_fuente, reglas_organizacion, contexto):
    """Tuplas completas propias; Índice por identidad, sin recorrer filas por regla."""
    propias = _reglas_propias(reglas_organizacion, contexto)
    return _opciones_indexadas(filas_fuente, propias, _identidades_lectura(filas_fuente))


def _copiar_fila_lectura(fila):
    # Las columnas SQL son escalares. Conservar también el contrato de entradas
    # anidadas sin pagar deepcopy por cada columna escalar de cada fila.
    return {k: deepcopy(v) if isinstance(v, (dict, list, set, tuple)) else v
            for k, v in fila.items()}


def validar_edicion(fuente, parche, opciones):
    """Devuelve dict completo válido; errores fijos de parche/combinación."""
    final = _aplicar_parche(fuente, parche)
    tupla = _tupla_clasificacion(final)
    if tupla is None or tupla not in opciones:
        raise ErrorClasificacion("COMBINACION_INVALIDA")
    return final


def clasificacion_efectiva(filas_fuente, reglas_organizacion, contexto):
    """Lista de copias con efectiva, procedencia, revision e identidad.

    Los originales se conservan. Una identidad ambigua jamás propaga un parche.
    contexto solo lo entrega el servidor; las funciones puras no autorizan usuarios.
    """
    propias = _reglas_propias(reglas_organizacion, contexto)
    opciones = opciones_clasificacion(filas_fuente, reglas_organizacion, contexto)
    salida = []
    for fila in filas_fuente:
        identidad = identidad_instrumento(fila, {"apariciones": filas_fuente})
        reglas = [r for r in propias if r["instrumento"] == identidad["clave"]]
        if len(reglas) > 1:
            raise ErrorClasificacion("REGLAS_AMBIGUAS")
        regla = reglas[0] if reglas and identidad["estado"] == "inequivoco" else None
        efectiva = (validar_edicion(fila, regla["parche"], opciones) if regla
                    else {k: deepcopy(fila.get(k)) for k in CAMPOS_CLASIFICACION})
        efectiva["sin_clasificar"] = _tupla_clasificacion(efectiva) is None
        salida.append({**deepcopy(fila), "efectiva": efectiva, "identidad": identidad,
                       "procedencia": "organizacion" if regla else "fuente",
                       "revision": regla.get("version") if regla else None})
    return salida


def novedad_fuente(fuente, regla, decisiones, contexto):
    """decisiones: mapping de repositorio falso, solo lectura.

    Clave (org, dueno, instrumento, tupla_fuente); valor base/aceptar/conservar.
    Devuelve pendiente con tuplas fuente/privada o None. No actualiza la base.
    """
    propias = _reglas_propias([regla] if regla else [], contexto)
    actual = _tupla_clasificacion(fuente)
    if not propias or not regla.get("parche") or actual is None:
        return None
    privada = _tupla_clasificacion(_aplicar_parche(fuente, regla["parche"]))
    base = _tupla_clasificacion(regla.get("fuente_base"))
    clave = (*_contexto_clasificacion(contexto), regla["instrumento"], actual)
    if actual in (base, privada) or decisiones.get(clave) in ("base", "aceptar", "conservar"):
        return None
    return {"clave_decision": clave, "fuente": actual, "privada": privada,
            "version_regla": regla.get("version")}

def componer_carteras(filas_fuente, snapshot, contexto):
    """U29: núcleo puro, sin consumidores. Una cartera completa por fondo.

    Snapshot U26 con registros comunes y reglas privadas del contexto servidor.
    Conserva filas originales; sumas/tenencias/contadores salen solo de efectiva.
    Ausencias y ambigüedades bloquean el parche, nunca la consulta de la fuente.
    """
    from fuentes_consulta import SIN_CLASIFICAR, rubro_de_cartera

    _contexto_clasificacion(contexto)
    if snapshot['estado'] != 'habilitado':
        return deepcopy(snapshot)
    propias = _reglas_propias(snapshot['reglas'], contexto)
    todas = filas_fuente + [f['fuente'] for f in snapshot['fuentes']]
    # Sin reglas propias solo hace falta la identidad de las filas comunes.
    identidades = _identidades_lectura(todas if propias else filas_fuente)
    tuplas = {}
    opciones = _opciones_indexadas(todas, propias, identidades, tuplas)
    por_instrumento = {}
    for regla in propias:
        por_instrumento.setdefault(regla['instrumento'], []).append(regla)
    registros = {}
    for registro in snapshot['fuentes']:
        registros.setdefault(tuple(registro['componentes']), []).append(registro)
    componentes, cantidades, fechas = [], {}, {}
    for fila in filas_fuente:
        codigo = fila['codigo_cnv']
        fecha = fila.get('fecha_cartera')
        if codigo in fechas and fechas[codigo] != fecha:
            raise ErrorClasificacion('SNAPSHOT_MEZCLADO')
        fechas[codigo] = fecha
        clave = identidades[id(fila)][0]
        componentes.append(clave)
        if clave:
            cantidades[clave] = cantidades.get(clave, 0) + 1
    filas, por_fondo, pendientes = [], {}, set()
    for fila, clave in zip(filas_fuente, componentes):
        candidatos = registros.get(clave, [])
        bloqueo = None
        if clave is None:
            bloqueo = 'IDENTIDAD_INCOMPLETA'
        elif cantidades[clave] > 1:
            bloqueo = 'IDENTIDAD_AMBIGUA'
        elif not candidatos:
            bloqueo = 'IDENTIDAD_AUSENTE'
        elif cantidades[clave] > 1 or len(candidatos) != 1 or candidatos[0]['ambiguo']:
            bloqueo = 'IDENTIDAD_AMBIGUA'
        registro = candidatos[0] if bloqueo is None else None
        identidad = {'componentes': clave, 'estado': bloqueo or 'inequivoco',
                     'instrumento_id': registro['instrumento_id'] if registro else None}
        serializada = identidades[id(fila)][1]
        reglas = por_instrumento.get(serializada, ()) if registro else ()
        if len(reglas) > 1:
            raise ErrorClasificacion('REGLAS_AMBIGUAS')
        regla = reglas[0] if reglas else None
        efectiva = (validar_edicion(fila, regla['parche'], opciones) if regla else
                    {k: fila.get(k) for k in CAMPOS_CLASIFICACION})
        # La marca fuente explícita también forma parte del contrato U25.
        efectiva['sin_clasificar'] = (_tupla_clasificacion(efectiva) is None if regla
                                     else tuplas[id(fila)] is None)
        # Sin regla propia no hay novedad posible (novedad_fuente devuelve None).
        pendiente = novedad_fuente(fila, regla, snapshot['decisiones'], contexto) if regla else None
        if pendiente:
            pendientes.add(pendiente['clave_decision'])
        salida = {**_copiar_fila_lectura(fila), 'efectiva': efectiva, 'identidad': identidad,
                  'bloqueo': bloqueo, 'procedencia': 'organizacion' if regla else 'fuente',
                  'revision': regla.get('version') if regla else None,
                  'parche': deepcopy(regla['parche']) if regla else {},
                  'version_fuente': registro['version_fuente'] if registro else None,
                  'pendiente': pendiente}
        filas.append(salida)
        por_fondo.setdefault(fila['codigo_cnv'], []).append(salida)
    resumenes = []
    for codigo, cartera in por_fondo.items():
        sumas, lineas = {}, []
        sin_clasificar, pct_sin_clasificar = 0, 0
        for fila in cartera:
            e, pct = fila['efectiva'], fila['pct_pn']
            categoria = e['categoria_resumen']
            sumas.setdefault(categoria, 0)
            if pct is not None:
                sumas[categoria] += pct
            lineas.append((SIN_CLASIFICAR if e['sin_clasificar'] else categoria,
                           e['categoria_detallada'], pct))
            if e['sin_clasificar']:
                sin_clasificar += 1
                pct_sin_clasificar += pct if pct is not None else 0
        rubro, mayores = rubro_de_cartera(lineas)
        resumenes.append(dict(codigo_cnv=codigo, sumas=sumas, tenencias=mayores, rubro=rubro,
                             instrumentos=len(cartera), sin_clasificar=sin_clasificar,
                             pct_sin_clasificar=pct_sin_clasificar,
                             pendientes=sum(f['pendiente'] is not None for f in cartera),
                             bloqueados=sum(f['bloqueo'] is not None for f in cartera)))
    return dict(estado='habilitado', filas=filas, resumenes=resumenes,
                pendientes=len(pendientes), opciones=opciones)


def parche_edicion_ficha(fila, valores, opciones):
    """Cambio parcial sobre la efectiva; conserva las excepciones ya existentes."""
    if (fila.get('bloqueo') or not fila.get('identidad', {}).get('instrumento_id')
            or fila['identidad'].get('estado') != 'inequivoco'):
        raise ErrorClasificacion('IDENTIDAD_BLOQUEADA')
    final = validar_edicion(fila['efectiva'], valores, opciones)
    cambios = {k: final[k] for k in CAMPOS_CLASIFICACION
               if final[k] != fila['efectiva'].get(k)}
    return {**fila.get('parche', {}), **cambios} if cambios else None


def filas_efectivas(filas):
    """Proyección para consumidores; originales y metadatos quedan en la vista U29."""
    return pd.DataFrame([{**f, **f['efectiva']} for f in filas])


def catalogo_efectivo(comunes, vista):
    salida = comunes.copy(deep=True)
    if vista['estado'] == 'habilitado':
        for r in vista['resumenes']:
            mascara = salida.codigo_cnv.eq(r['codigo_cnv'])
            salida.loc[mascara, 'rubro'] = r['rubro']
            if 'categoria' in salida:
                salida.loc[mascara, 'categoria'] = r['tenencias'][0][0] if r['tenencias'] else 'Sin cartera'
    return salida


def encabezado_efectivo(comun, vista, codigo):
    salida = comun.copy(deep=True)
    if vista['estado'] == 'habilitado' and not salida.empty:
        for r in vista['resumenes']:
            if r['codigo_cnv'] == codigo:
                for campo in ('instrumentos', 'sin_clasificar', 'pct_sin_clasificar'):
                    salida[campo] = r[campo]
    return salida


def resumenes_efectivos(cabeceras, vista):
    codigos = set(cabeceras.codigo_cnv)
    sumas, tenencias = [], []
    for r in vista['resumenes']:
        codigo = r['codigo_cnv']
        if codigo not in codigos:
            continue
        sumas.extend((codigo, cat, pct) for cat, pct in r['sumas'].items())
        tenencias.extend((codigo, i, det, pct) for i, (det, _, pct) in enumerate(r['tenencias'], 1))
    return (cabeceras.copy(deep=True),
            pd.DataFrame(sumas, columns=['codigo_cnv', 'categoria_resumen', 'pct']),
            pd.DataFrame(tenencias, columns=['codigo_cnv', 'posicion', 'categoria_detallada', 'pct_pn']))


# U12: lista de códigos CNV (int), de un solo uso, dentro de la sesión autenticada.
SELECCION_INFORME = "informe_desde_comparador"
SELECCION_GESTION = "gestion_desde_comparador"


def enviar_a_gestion(sesion, codigos):
    sesion[SELECCION_GESTION] = list(dict.fromkeys(int(c) for c in codigos))


def recibir_en_gestion(sesion):
    """Un pase nuevo activa comparados; luego respeta el modo elegido en Gestión."""
    transferencia = sesion.get(SELECCION_GESTION, [])
    if not transferencia:
        sesion.pop('gestion_transferencia', None)
    elif sesion.get('gestion_transferencia') != transferencia:
        sesion['gestion_transferencia'] = list(transferencia)
        sesion['gestion_modo'] = 'Tomar fondos comparados'
        sesion['gestion_situacion'] = 'Todos'
        sesion['gestion_situacion_memoria'] = 'Todos'
    return transferencia


def enviar_al_informe(sesion, codigos):
    nuevos = list(dict.fromkeys(int(c) for c in codigos))
    if sesion.get(SELECCION_INFORME) != nuevos:
        sesion[SELECCION_INFORME] = nuevos


def anexar_reglamentos(clases):
    """Empareja solo una clase explícita y única. Las pistas del nombre nunca son confirmación."""
    if '_reglamentos' not in clases:
        return clases
    clases = clases.copy()
    destinatarios, detalles = [], []
    for fila in clases.to_dict('records'):
        nombre = str(fila.get('nombre') or '')
        ley = re.search(r'\bclase\s+ley\s+(?:N[º°o.]?\s*)?27[.]?743', nombre, re.I)
        token = re.search(r'\bclase\s+([^\W_]+)', nombre, re.I)
        clave = 'LEY 27.743' if ley else token[1].upper() if token else None
        registros = fila.get('_reglamentos')
        candidatos = [r for r in registros if r.get('clase') == clave] if isinstance(registros, list) else []
        r = candidatos[0] if len(candidatos) == 1 else {}
        # A revisar: se muestra el estado, nunca una elegibilidad inferida como cierta.
        corto = ('A revisar' if r.get('estado') == 'a_revisar' else r.get('destinatario', ''))
        detalle = ''
        if r:
            detalle = (f"{r.get('fuente_documento', '')} · {r.get('fecha_reglamento') or 'fecha no informada'}"
                f" · {r.get('fuente', '')}"
                + (f" · revisión {r['fecha_revision']}" if r.get('fecha_revision') else '')
                + f" · {r.get('texto_origen', '')} · {r.get('url', '')}")
        destinatarios.append(corto)
        detalles.append(detalle)
    clases['destinatario'], clases['detalle_reglamento'] = destinatarios, detalles
    return clases.drop(columns='_reglamentos')


def etiqueta_destinatario(nombre, destinatario):
    return str(nombre) + (' · ' + str(destinatario) if isinstance(destinatario, str) and destinatario else '')


def detalle_destinatarios(clases):
    if 'destinatario' not in clases or not clases.destinatario.fillna('').ne('').any():
        return pd.DataFrame()
    cols = [c for c in ('nombre_fondo', 'nombre', 'destinatario', 'detalle_reglamento') if c in clases]
    return clases.loc[clases.destinatario.fillna('').ne(''), cols].rename(columns={
        'nombre_fondo': 'Fondo', 'nombre': 'Clase', 'destinatario': 'Destinatario', 'detalle_reglamento': 'Fuente y fecha'})


def etiquetas_grupos_clases(clases):
    grupos = agrupar_clases_comparador(clases)
    if 'destinatario' not in clases:
        return {n: n for n in grupos.unique()}
    textos = clases.assign(_grupo=grupos).groupby('_grupo').destinatario.agg(
        lambda s: ' / '.join(sorted(set(s.dropna()) - {''})))
    return {n: etiqueta_destinatario(n, t) for n, t in textos.items()}


def clases_comparador(fondos, moneda, leer_clases):
    partes = []
    registros = fondos.to_dict('records')
    for f in registros:
        partes.append(filtrar_moneda(leer_clases(int(f['codigo_cnv'])), moneda))
    resultado = pd.concat(partes, keys=range(len(partes)))
    resultado['codigo_cnv'] = resultado.index.get_level_values(0).map(
        {i: int(f['codigo_cnv']) for i, f in enumerate(registros)})
    resultado = resultado.reset_index(drop=True)
    resultado['nombre_fondo'] = resultado.codigo_cnv.map(fondos.set_index('codigo_cnv')['nombre'])
    resultado['etiqueta'] = (resultado['nombre_fondo'] + ' — '
                            + resultado['nombre'].str.split(' - ').str[-1]).astype(object)
    return resultado


def agrupar_clases_comparador(clases):
    """Primer token alfanumérico tras «Clase», completo y en mayúsculas.

    «Clase A Plus» → A; «Clase B1» → B1. Sin esa marca: «Sin clase».
    Prioridad: nombre/descripción de la clase, luego nombre del fondo.
    """
    grupos = pd.Series(pd.NA, index=clases.index, dtype='string')
    for campo in ('nombre', 'descripcion', 'clase', 'nombre_fondo', 'fondo'):
        if campo in clases:
            token = clases[campo].astype('string').str.extract(
                r'(?i)\bclase\s+([^\W_]+)', expand=False).str.upper()
            grupos = grupos.fillna(token)
    return grupos.fillna('Sin clase')


def proponer_fondos_comparador(fondos, clases, elegidas):
    if not elegidas:
        return fondos
    return fondos[fondos.codigo_cnv.isin(clases.loc[
        agrupar_clases_comparador(clases).isin(elegidas), 'codigo_cnv'])]


def historia_comparador(fondos, clases, leer_serie, cargar_puntual):
    """Fuentes inyectables; un fondo sin historia no interrumpe a los demás."""
    partes, avisos = [], []
    seguidas = clases.loc[clases.codigo_cnv.isin(fondos.loc[fondos.seguido, 'codigo_cnv']), 'codigo_cafci']
    lote = leer_serie(seguidas) if len(seguidas) else pd.DataFrame(columns=['codigo_cafci'])
    for fondo in fondos.to_dict("records"):
        codigos = clases.loc[clases.codigo_cnv.eq(fondo["codigo_cnv"]), "codigo_cafci"]
        if codigos.empty:
            continue
        if fondo["seguido"]:
            historia = lote
        else:
            resultado = cargar_puntual(fondo)
            historia = resultado["serie"]
            avisos += [f"{fondo['nombre']}: {a}" for a in resultado["avisos"]]
        historia = historia[historia.codigo_cafci.isin(codigos)]
        faltantes = set(codigos) - set(historia.codigo_cafci)
        if historia.empty:
            avisos.append(f"{fondo['nombre']}: sin historia disponible para las clases elegidas.")
        elif faltantes:
            avisos.append(f"{fondo['nombre']}: sin historia en las clases " + ", ".join(
                clases.loc[clases.codigo_cafci.isin(faltantes), "etiqueta"]) + ".")
        if not historia.empty:
            partes.append(historia)
    columnas = ["codigo_cafci", "fecha", "cuotaparte", "patrimonio"]
    serie = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=columnas)
    return serie.merge(clases[["codigo_cafci", "etiqueta", "moneda"]], on="codigo_cafci"), avisos


def controles_series(sesion, clases):
    """Identidad CAFCI y memoria independiente de widgets, período y modo."""
    import marca
    memoria = sesion.setdefault("comparador_series", {})
    paleta = marca.paleta()
    for codigo in sorted(set(int(c) for c in clases.codigo_cafci)):
        if codigo not in memoria:
            memoria[codigo] = {"color": paleta[len(memoria) % len(paleta)], "visible": True}
    return [{"codigo": int(c.codigo_cafci),
             "nombre": f"{c.etiqueta} · {c.moneda} · CAFCI {c.codigo_cafci}",
             "corto": f"{c.etiqueta} · {c.moneda}",
             **memoria[int(c.codigo_cafci)]} for c in clases.itertuples()]


def alternar_serie(sesion, codigo):
    estado = sesion["comparador_series"][codigo]
    estado["visible"] = not estado["visible"]


def controles_fondos_comparador(sesion, clases, fondos=()):
    """Un encendido y color por fondo; las clases conservan su identidad CAFCI."""
    import marca
    memoria = sesion.setdefault('comparador_fondos_series', {})
    paleta = marca.paleta()
    for codigo in sorted(set(int(c) for c in [*clases.codigo_cnv, *fondos])):
        memoria.setdefault(codigo, {'color': paleta[len(memoria) % len(paleta)], 'visible': True})
    controles = controles_series(sesion, clases)
    fondo_de = clases.set_index('codigo_cafci').codigo_cnv.to_dict()
    for c in controles:
        c.update(memoria[int(fondo_de[c['codigo']])])
        sesion['comparador_series'][c['codigo']].update(color=c['color'], visible=c['visible'])
    return controles


def alternar_fondo_comparador(sesion, codigo):
    estado = sesion['comparador_fondos_series'][codigo]
    estado['visible'] = not estado['visible']


def quitar_fondo_comparador(sesion, codigo):
    actuales = sesion.get('comparador_fondos', sesion.get('comparador_fondos_memoria', []))
    sesion['comparador_fondos'] = [c for c in actuales if c != codigo]
    sesion['comparador_fondos_memoria'] = list(sesion['comparador_fondos'])


PLAZOS_COMPARADOR = (('7 días', 7), ('30 días', 30), ('60 días', 60),
                     ('90 días', 90), ('6 meses', 182), ('1 año', 365))


def barras_comparador(serie, clases, controles, modo="R", hasta=None):
    """Primera clase por fondo: rendimiento o promedio simple de patrimonios diarios.

    Reutiliza base_100; exige dos valores y cobertura del plazo con la tolerancia
    habitual. No convierte una historia corta en rendimiento de un año.
    """
    campo = 'Patrimonio promedio' if modo == 'P' else 'Rendimiento %'
    columnas = ['Plazo', 'codigo_cnv', 'Fondo', 'Clase', 'Color', 'Moneda', campo]
    if serie.empty or clases.empty:
        return pd.DataFrame(columns=columnas)
    visibles = {c['codigo']: c for c in controles if c['visible']}
    representantes = clases.drop_duplicates('codigo_cnv')
    representantes = representantes[representantes.codigo_cafci.isin(visibles)]
    hasta = hasta or serie.fecha.max()
    filas = []
    for plazo, dias in PLAZOS_COMPARADOR:
        desde = hasta - timedelta(days=dias)
        normalizada = (base_100(serie[serie.codigo_cafci.isin(representantes.codigo_cafci)], desde, hasta)
                       if modo == "R" else None)
        grupo = []
        for c in representantes.itertuples():
            if modo == 'P':
                valores = serie.loc[serie.codigo_cafci.eq(c.codigo_cafci)
                    & serie.fecha.between(desde, hasta)].dropna(subset=['patrimonio'])
                if valores.empty:
                    continue
                # Una observación por día; los nulos nunca suman ni cuentan.
                valor = float(valores.drop_duplicates('fecha').patrimonio.mean())
            else:
                valores = normalizada[normalizada.codigo_cafci.eq(c.codigo_cafci)].dropna(subset=['base 100'])
                if (len(valores) < 2 or valores.fecha.min() > desde + timedelta(days=TOLERANCIA_DIAS)
                        or valores.fecha.max() < hasta - timedelta(days=TOLERANCIA_DIAS)):
                    continue
                valor = float(valores['base 100'].iloc[-1] - 100)
            grupo.append({'Plazo': plazo, 'codigo_cnv': int(c.codigo_cnv),
                          'Fondo': c.etiqueta.rsplit(' — ', 1)[0], 'Clase': c.etiqueta,
                          'Color': visibles[c.codigo_cafci]['color'], 'Moneda': c.moneda,
                          campo: valor})
        filas.extend(sorted(grupo, key=lambda f: (-f[campo], f['codigo_cnv'])))
    return pd.DataFrame(filas, columns=columnas)


def comparar(serie, desde, hasta, modo, controles=None):
    """Datos comunes a pantalla y PDF: R base 100, P en moneda de cada clase."""
    import plotly.express as px
    import marca
    serie = base_100(serie, desde, hasta)
    trazos = serie
    colores = None
    if controles is not None:
        nombres = {c["codigo"]: c["nombre"] for c in controles}
        trazos = serie.assign(etiqueta=serie.codigo_cafci.map(nombres))
        colores = {c["nombre"]: c["color"] for c in controles}
    especificaciones = []
    if modo in ("R", "R y P"):
        especificaciones.append(("base 100", "Rendimiento (base 100)"))
    if modo in ("P", "R y P"):
        especificaciones.append(("patrimonio", "Patrimonio (moneda de cada clase)"))
    graficos = []
    for campo, titulo in especificaciones:
        fig = px.line(trazos, x="fecha", y=campo, color="etiqueta", title=titulo,
                      color_discrete_map=colores,
                      color_discrete_sequence=marca.paleta(),
                      facet_row="moneda" if campo == "patrimonio" else None)
        fig.update_traces(connectgaps=False)
        if controles is not None:
            visibles = {c["nombre"]: c["visible"] for c in controles}
            for trazo in fig.data:
                trazo.visible = visibles[trazo.legendgroup]
            fig.update_layout(showlegend=False)
        if campo == "patrimonio":
            fig.update_yaxes(matches=None)
        fig.update_xaxes(range=[desde, hasta])
        fig.update_layout(legend_title_text="", margin=dict(t=50, b=10))
        graficos.append(fig)
    resumen = serie.groupby(["etiqueta", "moneda"], sort=False).agg(
        Desde=("fecha", "min"), Hasta=("fecha", "max"), Datos=("fecha", "count"),
        inicial=("cuotaparte", "first"), final=("cuotaparte", "last"),
        **{"Último valor de patrimonio": ("patrimonio", lambda s: s.iloc[-1]),
           "Fecha del patrimonio": ("fecha", "max")}).reset_index()
    resumen["Rendimiento %"] = (resumen.pop("final") / resumen.pop("inicial") - 1) * 100
    return serie, resumen.rename(columns={"etiqueta": "Clase", "moneda": "Moneda"}), graficos

PESOS = ("ARS",)
DOLARES = ("USD", "USB")           # Dólares incluye USD y USD billete
OPCIONES_MONEDA = ("Pesos", "Dólares")
NOMBRE_MONEDA = {"ARS": "Pesos", "USD": "Dólares", "USB": "Dólares billete"}
SIMBOLO_MONEDA = {"ARS": "$", "USD": "USD", "USB": "USD"}
REGIONES = {"Arg": "Argentina", "Glo": "Global", "Latam": "Latinoamérica", "Eur": "Europa", "Bra": "Brasil",
            "Eeuu": "Estados Unidos"}
HORIZONTES = {"Cor": "Corto plazo", "Med": "Mediano plazo", "Lar": "Largo plazo", "Flex": "Flexible",
              "Sasig": "Sin asignar"}
TOLERANCIA_DIAS = 4                # fines de semana largos: no se avisa por menos de esto


def monedas_de(opcion):
    """'Pesos' -> ('ARS',) ; 'Dólares' -> ('USD', 'USB') ; 'Todos' -> None (sin filtro)."""
    return {"Pesos": PESOS, "Dólares": DOLARES}.get(opcion)


def filtrar_moneda(df, opcion, columna="moneda"):
    """Deja solo los fondos o clases de esa moneda ('Todos' no filtra)."""
    if isinstance(opcion, (list, tuple)):
        if not opcion:
            return df
        codigos = set(c for o in opcion for c in (monedas_de(o) or ()))
        return df[df[columna].isin(codigos)]
    codigos = monedas_de(opcion)
    return df if codigos is None else df[df[columna].isin(codigos)]


def aviso_monedas(monedas):
    """Compara familias de moneda, separando variantes del dólar."""
    textos = {str(m).strip().upper() for m in monedas if pd.notna(m)}
    pesos = any(t in ('ARS', '$', 'PESOS', 'PESO') for t in textos)
    dolares = {t for t in textos if t in ('USD', 'USB', 'US$', 'U$S') or 'DÓLAR' in t or 'DOLAR' in t or 'MEP' in t or 'CABLE' in t}
    if pesos and dolares:
        return 'Estás comparando monedas distintas: los rendimientos y patrimonios no son directamente comparables.'
    if any('MEP' in t for t in dolares) and any('CABLE' in t for t in dolares):
        return 'Estás comparando dólares de distinto tipo (MEP y cable).'
    return None


def filtrar_por_rubros(df, rubros, columna="categoria_resumen"):
    """Filtro de la torta: sin rubros elegidos devuelve todo; con uno o más, solo los instrumentos de esos rubros."""
    rubros = list(rubros or [])
    return df if not rubros else df[df[columna].isin(rubros)]


def historia_desde(por_clase):
    """Primera fecha con cuotaparte numérica entre las clases activas.

    por_clase: DataFrame con 'primera' y 'ultima' (de las filas con cuotaparte numérica, nunca las que solo
    traen un estado). Activas = las que tienen datos hasta 10 días antes del último dato."""
    if por_clase.empty:
        return None
    activas = por_clase[por_clase["ultima"] >= por_clase["ultima"].max() - timedelta(days=10)]
    return activas["primera"].min()


def validar_periodo(desde, hasta):
    """Texto de aviso si el período no sirve (desde posterior o igual a hasta); None si está bien."""
    if desde is None or hasta is None:
        return "Elegí las dos fechas del período."
    if desde >= hasta:
        return (f"La fecha \"desde\" ({formato.fecha(desde)}) tiene que ser anterior a \"hasta\" "
                f"({formato.fecha(hasta)}). Corregí las fechas.")
    return None


def base_100(ser, desde, hasta=None):
    """Serie recortada a [desde, hasta] con cada clase en base 100 desde su primer valor de ese período."""
    s = ser[ser["fecha"] >= desde]
    if hasta is not None:
        s = s[s["fecha"] <= hasta]
    s = s.sort_values(["codigo_cafci", "fecha"]).copy()
    def normalizar(valores):
        validos = pd.to_numeric(valores, errors="coerce")
        validos = validos.where((validos > 0) & (validos < float("inf")))
        return validos / validos.dropna().iloc[0] * 100 if validos.notna().any() else validos
    s["base 100"] = s.groupby("codigo_cafci")["cuotaparte"].transform(normalizar)
    return s


def historia_insuficiente(serie):
    """Alguna clase tiene menos de dos cuotapartes válidas para mostrar evolución."""
    return serie.empty or bool((serie.groupby("codigo_cafci")["base 100"].count() < 2).any())


def clases_sin_cobertura(ser, desde, hasta):
    """Etiquetas de las clases que no tienen datos en todo el período (empiezan tarde o terminan antes)."""
    tol = timedelta(days=TOLERANCIA_DIAS)
    out = []
    for etiqueta, g in ser.groupby("etiqueta"):
        if g["fecha"].min() > desde + tol or g["fecha"].max() < hasta - tol:
            out.append(etiqueta)
    return sorted(out)


def _texto(x):
    return formato.texto_o_guion(x)


LEYENDA_CAFCI = "Cartera parcial (CAFCI): principales tenencias + resto sin detalle"


def leyenda_cartera(fuentes):
    """La fuente viaja con la lectura común; no agrega consultas ni cachés."""
    if isinstance(fuentes, str):
        fuentes = [fuentes]
    return LEYENDA_CAFCI if any(f == 'cafci' for f in fuentes if isinstance(f, str)) else ''


def cabecera_fondo(f):
    """Datos de la cabecera de la ficha como [(etiqueta, valor)]; ningún valor queda vacío (se muestra '—').

    'Clasificación CNV' sale solo de la ficha de la CNV; si no la informa, se muestra únicamente
    'Tipo de fondo (CAFCI)' (nunca el tipo de la CAFCI con la etiqueta CNV)."""
    moneda = f.get("moneda")
    simbolo = SIMBOLO_MONEDA.get(moneda, "")
    pn = formato.VACIO if formato._es_nulo(f.get("patrimonio_neto")) else f"{simbolo} {formato.entero(f['patrimonio_neto'])}".strip()
    fuentes = []
    if not formato._es_nulo(f.get("fecha_valores")):
        fuentes.append(f"CAFCI (valores al {formato.fecha(f['fecha_valores'])})")
    if not formato._es_nulo(f.get("fecha_cartera")):
        fuentes.append(f"{'CAFCI parcial' if f.get('fuente_cartera') == 'cafci' else 'CNV'} (cartera al {formato.fecha(f['fecha_cartera'])})")
    datos = [
        ("Cartera al", formato.fecha(f.get("fecha_cartera"))),
        ("Gerente", _texto(f.get("gerente"))),
        ("Depositaria", _texto(f.get("depositaria"))),
    ]
    if not formato._es_nulo(f.get("clasificacion_cnv")) and str(f["clasificacion_cnv"]).strip():
        datos.append(("Clasificación CNV", str(f["clasificacion_cnv"]).strip()))
    datos += [
        ("Tipo de fondo (CAFCI)", _texto(f.get("tipo_fondo"))),
        ("Categoría", _texto(f.get("categoria"))),
        ("Moneda", NOMBRE_MONEDA.get(moneda, formato.VACIO)),
        ("Región", REGIONES.get(f.get("region"), _texto(f.get("region")))),
        ("Horizonte", HORIZONTES.get(f.get("horizonte"), _texto(f.get("horizonte")))),
        ("Patrimonio neto", pn),
        ("Fuente", " · ".join(fuentes) or formato.VACIO),
    ]
    return datos


NOMBRES_FICHA_UNICA = {
    'plazo_rescate': 'Plazo de rescate', 'moneda': 'Moneda', 'monto_minimo': 'Monto mínimo',
    'riesgo': 'Riesgo', 'perfil': 'Perfil del inversor', 'objetivo_politica': 'Objetivo y política de inversión',
    'patrimonio_neto': 'Patrimonio neto', 'clasificacion': 'Clasificación', 'horizonte': 'Horizonte de inversión',
    'registro_cnv': 'Registro CNV', 'fecha_inicio': 'Inicio de operaciones', 'auditor': 'Auditor',
    'correo': 'Correo de contacto', 'depositaria': 'Sociedad depositaria', 'gerente': 'Sociedad gerente',
    'calificacion': 'Calificación', 'calificacion_y_benchmark': 'Calificación y referencia de rendimiento',
    'benchmark': 'Referencia de rendimiento', 'rendimiento': 'Rendimiento',
    'honorarios_gerente_y_depositaria': 'Honorarios de gerente y depositaria',
    'honorarios_gerente': 'Honorarios de gerente', 'honorarios_depositaria': 'Honorarios de depositaria',
    'comisiones_suscripcion_rescate_transferencia': 'Comisiones de suscripción, rescate y transferencia',
    'comision_suscripcion': 'Comisión de suscripción', 'comision_rescate': 'Comisión de rescate',
    'comision_transferencia': 'Comisión de transferencia', 'honorarios_exito': 'Honorarios de éxito',
    'gastos_ordinarios_3m': 'Gastos ordinarios de gestión (3 meses)',
    'horario': 'Horario', 'horarios': 'Horarios', 'agencia': 'Agencia', 'fecha_calificacion': 'Fecha de calificación',
}


def nombre_dato_ficha(campo):
    partes = campo.split(':')
    return ' · '.join([NOMBRES_FICHA_UNICA.get(partes[0], partes[0].replace('_', ' ').capitalize()), *partes[1:]])


def valor_dato_ficha(dato, campo='valor_normalizado'):
    if dato.get('estado') == 'dudoso':
        return 'dudoso'
    if dato.get('estado') != 'leido':
        return 'no disponible en la ficha'
    valor = dato.get(campo)
    if valor is None or str(valor).strip() == '':
        valor = dato.get('valor_literal')
    return valor if valor is not None and str(valor).strip() else 'no disponible en la ficha'


def tablas_ficha_unica(ficha):
    """Tablas de presentación sin completar faltantes con ceros ni sumar grupos."""
    condiciones, calificacion = [], []
    for d in ficha['datos']:
        fila = {'Dato': nombre_dato_ficha(d['campo']), 'Valor': valor_dato_ficha(d)}
        destino = calificacion if any(x in d['campo'] for x in ('calificacion', 'agencia')) else condiciones
        destino.append(fila)
    honorarios = {}
    clases = sorted({h['clase'] for h in ficha['honorarios']})
    for h in ficha['honorarios']:
        concepto = nombre_dato_ficha(h['concepto'])
        fila = honorarios.setdefault(concepto, {'Concepto': concepto, **{c: 'no disponible en la ficha' for c in clases}})
        fila[h['clase']] = valor_dato_ficha(h, 'valor_literal')
    tenencias = [{'Tipo': 'Subclase' if t['tipo'] == 'subclase' else 'Tenencia',
                  'Nombre': 'Resto' if t['es_resto'] else t['nombre'],
                  '%': valor_dato_ficha(t, 'pct')} for t in ficha['tenencias']]
    return [pd.DataFrame(filas) for filas in (condiciones, list(honorarios.values()), calificacion, tenencias)]


def clase_de_nombre(nombre):
    """Letra/sufijo explícito; nunca empareja una clase desconocida con todas."""
    texto = str(nombre).strip()
    coincidencia = re.search(r'\bclase\s+(.+)$', texto, re.I)
    return coincidencia[1].strip() if coincidencia else texto


def normalizar_complemento(valor):
    """Equivalencias conservadoras para comparar fuentes, nunca para reemplazarlas."""
    if formato._es_nulo(valor):
        return ''
    texto = re.sub(r'\s+', ' ', _sin_acentos(str(valor)).casefold()).strip()
    if texto in ('', '—', '-', 'no disponible', 'no disponible en la ficha', 'dudoso'):
        return ''
    texto = {'pesos': 'ars', 'pesos argentinos': 'ars', '$': 'ars',
             'dolares': 'usd', 'dolares estadounidenses': 'usd',
             'corto': 'corto plazo', 'mediano': 'mediano plazo', 'largo': 'largo plazo',
             'inmediato': 't+0', 'inmediata': 't+0', 'en el dia': 't+0',
             'sin asignar': ''}.get(texto, texto)
    plazo = re.fullmatch(r'(?:t\s*\+\s*(\d+)|(\d+) dias? habil(?:es)?|(\d+) horas?)', texto)
    if plazo:
        dias = plazo[1] or plazo[2]
        if dias or int(plazo[3]) % 24 == 0:
            return 't+' + str(int(dias) if dias else int(plazo[3]) // 24)
    numero = re.sub(r'\s*(?:anual(?:es)?|p\.?a\.?)$', '', texto).replace('%', '').strip()
    if re.fullmatch(r'[-+]?\d[\d.,]*', numero):
        if ',' in numero:
            numero = numero.replace('.', '').replace(',', '.')
        elif '%' not in texto and re.fullmatch(r'[1-9]\d{0,2}(?:\.\d{3})+', numero):
            numero = numero.replace('.', '')
        try:
            return str(float(numero))
        except ValueError:
            pass
    return texto


def texto_complemento(dato, campo=None):
    """Solo valores seguros; números y fechas con formato argentino."""
    if dato.get('estado') != 'leido':
        return ''
    valor = dato.get('valor_normalizado')
    if formato._es_nulo(valor) or str(valor).strip() == '':
        valor = dato.get('valor_literal')
    if not normalizar_complemento(valor):
        return ''
    texto = str(valor).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', texto):
        return formato.fecha(texto)
    if re.fullmatch(r'[-+]?\d+(?:\.\d+)?', texto):
        n = float(texto)
        texto = formato.entero(n) if n.is_integer() else formato.numero(n, 4).rstrip('0').rstrip(',')
        if campo and (campo.startswith(('honorarios', 'comision', 'gastos'))):
            texto += ' %'
    texto = re.sub(r'\bdias\b', 'días', texto, flags=re.I)
    texto = re.sub(r'\bdia\b', 'día', texto, flags=re.I)
    texto = re.sub(r'\bhabiles\b', 'hábiles', texto, flags=re.I)
    texto = re.sub(r'\bhabil\b', 'hábil', texto, flags=re.I)
    def porcentaje(m):
        numero = m[1].replace('.', '').replace(',', '.') if ',' in m[1] else m[1]
        return formato.numero_corto(float(numero)) + ' %'
    texto = re.sub(r'(\d+(?:[.,]\d+)*)\s*%', porcentaje, texto)
    return texto


def descripcion_fondo(fondo, ficha=None):
    """Texto efectivo único, con procedencia; nunca modifica el respaldo privado."""
    ficha = ficha if ficha is not None else fondo.get('_ficha_unica')
    if isinstance(ficha, dict):
        for campo in ('objetivo_politica', 'descripcion', 'politica_inversion', 'objetivo'):
            for dato in ficha.get('datos', []):
                texto = texto_complemento(dato)
                if dato.get('campo') == campo and texto:
                    fuente = {'gerente': 'web del gerente', 'cafci': 'CAFCI'}.get(
                        ficha.get('fuente'), ficha.get('fuente') or 'Ficha Única')
                    return texto, f"Ficha Única al {formato.fecha(ficha.get('fecha_referencia'))} · fuente: {fuente}"
    textos = []
    for campo in ('descripcion_breve', 'tematica'):
        texto = _texto(fondo.get(campo))
        if texto != formato.VACIO and normalizar_complemento(texto) not in {
                normalizar_complemento(t) for t in textos}:
            textos.append(texto)
    return ' · '.join(textos) or formato.VACIO, ''


def descripcion_visible(fondo, ficha=None):
    texto, fuente = descripcion_fondo(fondo, ficha)
    return texto + (' (' + fuente + ')' if fuente else '')


def textos_fondo(fondo, ficha=None):
    """(descripción, idea) para tablas con dos columnas: con ficha va todo en la primera; nunca se repite."""
    texto, fuente = descripcion_fondo(fondo, ficha)
    if fuente:
        return texto + ' (' + fuente + ')', formato.VACIO
    idea = tematica(fondo)[1]
    descripcion = _texto(fondo.get('descripcion_breve'))
    if descripcion != formato.VACIO and normalizar_complemento(descripcion) == normalizar_complemento(idea):
        idea = formato.VACIO
    return descripcion, idea


def lineas_descripcion(fondo, ficha=None, marca=True):
    """Markdown de pantalla/PDF: con ficha una sola línea; sin ficha, lo escrito al pedir el fondo."""
    if descripcion_fondo(fondo, ficha)[1]:
        return [('Descripción breve', textos_fondo(fondo, ficha)[0])]
    descripcion, idea = tematica(fondo if marca else dict(fondo, tematica_provisoria=False))
    if descripcion != formato.VACIO and normalizar_complemento(descripcion) == normalizar_complemento(idea):
        idea = formato.VACIO
    return [('Descripción breve', descripcion), ('Idea y temática', idea)]


def actividad_clases(historia, hoy=None):
    """Actividad de 30 días corridos; también acepta el resumen de la lectura común."""
    if historia.empty:
        return pd.Series(dtype=bool)
    h = historia.copy()
    if {'cantidad_min', 'cantidad_max', 'patrimonio_ultimo'} <= set(h):
        return h.set_index('codigo_cafci').eval(
            'patrimonio_ultimo > 0 and cantidad_max > cantidad_min').fillna(False)
    h['fecha'] = pd.to_datetime(h.fecha)
    cierre = pd.Timestamp(hoy).normalize() if hoy is not None else pd.Timestamp.today().normalize()
    h = h[h.fecha.between(cierre - pd.Timedelta(days=29), cierre)].sort_values('fecha')
    for campo in ('patrimonio', 'cuotaparte', 'cantidad_cuotapartes'):
        h[campo] = pd.to_numeric(h.get(campo, pd.Series(index=h.index, dtype=float)), errors='coerce')
    h['cantidad'] = h.cantidad_cuotapartes.fillna(h.patrimonio.div(h.cuotaparte.where(h.cuotaparte.gt(0))))
    grupos = h.groupby('codigo_cafci')
    cantidades = grupos.cantidad.agg(['min', 'max'])
    ultimo = h.drop_duplicates('codigo_cafci', keep='last').set_index('codigo_cafci').patrimonio
    return (ultimo.gt(0) & cantidades['max'].gt(cantidades['min'])).fillna(False)


def integrar_ficha_unica(fondo, ficha):
    """Complementa la cabecera y los costos sin repetir ni sustituir datos propios."""
    resultado = dict(cabecera=cabecera_fondo(fondo), extras=[], avisos=[],
                     descripcion=descripcion_fondo(fondo, ficha)[0], sugerencia='', costos=pd.DataFrame())
    if not ficha:
        return resultado
    fecha = formato.fecha(ficha['fecha_referencia'])
    mapa = {'gerente': 'Gerente', 'depositaria': 'Depositaria', 'moneda': 'Moneda',
            'horizonte': 'Horizonte', 'clasificacion': 'Clasificación CNV'}
    cabecera = dict(resultado['cabecera'])

    def comparar(etiqueta, nuevo, anterior):
        if normalizar_complemento(anterior) and normalizar_complemento(nuevo) != normalizar_complemento(anterior):
            resultado['avisos'].append(
                f'{etiqueta}: Ficha Única al {fecha} dice: {nuevo} (la app muestra: {anterior})')

    permitidos = {'auditor', 'fecha_inicio', 'perfil', 'riesgo', 'plazo_rescate', 'monto_minimo',
                  'calificacion', 'agencia', 'fecha_calificacion', 'benchmark'}
    for d in ficha.get('datos', []):
        campo = d['campo']
        nuevo = texto_complemento(d, campo)
        if not nuevo:
            continue
        if campo in ('objetivo_politica', 'descripcion', 'politica_inversion', 'objetivo'):
            continue
        elif campo in mapa:
            etiqueta = mapa[campo]
            anterior = cabecera.get(etiqueta, '')
            if not normalizar_complemento(anterior):
                cabecera[etiqueta] = nuevo
            else:
                comparar(etiqueta, nuevo, anterior)
        elif campo in permitidos:
            anterior = fondo.get(campo)
            if campo == 'calificacion':
                calificaciones = {str(c['calificacion']).strip() for c in ficha.get('clases', [])
                                  if normalizar_complemento(c.get('calificacion'))}
                if calificaciones:
                    for calificacion in sorted(calificaciones):
                        comparar(nombre_dato_ficha(campo), nuevo, calificacion)
                    continue
            if normalizar_complemento(anterior):
                comparar(nombre_dato_ficha(campo), nuevo, anterior)
            else:
                resultado['extras'].append((nombre_dato_ficha(campo), nuevo))
    resultado['cabecera'] = list(cabecera.items())
    conceptos = {'honorarios_gerente': 'honorarios_sg', 'honorarios_depositaria': 'honorarios_sd',
        'gastos_ordinarios': 'gastos_ordinarios', 'comision_suscripcion': 'comision_ingreso',
        'comision_rescate': 'comision_rescate', 'comision_transferencia': 'comision_transferencia',
        'honorarios_exito': 'honorarios_exito'}
    # Cada clase conserva sus costos; los combinados de la ficha nunca se descomponen.
    filas = {}
    for c in ficha.get('clases', []):
        clase = clase_de_nombre(c['nombre'])
        fila = {'Clase': c['nombre']}
        for campo in ('calificacion', 'marca_estado'):
            if normalizar_complemento(c.get(campo)):
                fila['Calificación' if campo == 'calificacion' else 'Estado CAFCI'] = str(c[campo])
        for concepto, columna in conceptos.items():
            valor = c.get(columna)
            if not formato._es_nulo(valor):
                fila[nombre_dato_ficha(concepto)] = texto_complemento(
                    dict(estado='leido', valor_normalizado=valor), concepto)
        filas[clase] = fila
    for h in ficha.get('honorarios', []):
        concepto, clase = h['concepto'], h['clase']
        nuevo = texto_complemento(h, concepto)
        if not nuevo:
            continue
        fila = filas.setdefault(clase, {'Clase': clase})
        etiqueta = nombre_dato_ficha(concepto)
        if etiqueta in fila:
            comparar(f'{etiqueta} · {clase}', nuevo, fila[etiqueta])
        else:
            fila[etiqueta] = nuevo
    if filas and any(len(f) > 1 for f in filas.values()):
        resultado['costos'] = pd.DataFrame(filas.values()).fillna(formato.VACIO)
    return resultado


def control_rendimientos_ficha(ficha, historia, clases):
    """Compara retornos directos al cierre de la ficha, sin lecturas adicionales.

    Omite TNA, períodos desconocidos, cierres ausentes o historia insuficiente.
    Devuelve las diferencias mayores a 0,05 puntos porcentuales.
    """
    if not ficha or historia.empty or clases.empty:
        return []
    cierre = pd.Timestamp(ficha['fecha_referencia'])
    periodos = {'7 dias': pd.Timedelta(days=7), '1 mes': pd.Timedelta(days=30),
                '90 dias': pd.Timedelta(days=90), '180 dias': pd.Timedelta(days=180),
                '12 meses': pd.DateOffset(years=1),
                'en el año': cierre - pd.Timestamp(cierre.year - 1, 12, 31)}
    codigos = {clase_de_nombre(c['nombre']): c['codigo_cafci'] for c in clases.to_dict('records')}
    avisos = []
    for d in ficha.get('datos', []):
        partes = d['campo'].split(':')
        if len(partes) != 3 or partes[0] != 'rendimiento' or d.get('estado') != 'leido':
            continue
        _, clase, periodo = partes
        valor = str(d.get('valor_normalizado') or '').removeprefix('Directo ').strip()
        if clase not in codigos or periodo not in periodos or not re.fullmatch(r'-?\d+(?:\.\d+)?', valor):
            continue
        serie = historia[historia.codigo_cafci.eq(codigos[clase])].copy()
        serie['fecha'] = pd.to_datetime(serie.fecha)
        if 'patrimonio' in serie:
            serie = serie[serie.patrimonio.ne(0)]
        if 'marca_estado' in serie:
            serie = serie[serie.marca_estado.fillna('').eq('')]
        serie = serie[serie.cuotaparte.notna() & serie.cuotaparte.gt(0)]
        serie = serie[serie.fecha.le(cierre)].sort_values('fecha')
        if serie.empty or serie.iloc[-1].fecha != cierre:
            continue
        base = serie[serie.fecha.le(cierre - periodos[periodo])]
        if base.empty or (cierre - periodos[periodo] - base.iloc[-1].fecha).days > 10:
            continue
        inicial, final = base.iloc[-1].cuotaparte, serie.iloc[-1].cuotaparte
        if pd.isna(inicial) or pd.isna(final) or inicial <= 0 or final <= 0:
            continue
        diferencia = abs((float(final) / float(inicial) - 1) * 100 - float(valor))
        if diferencia > 0.05 + 1e-9:
            avisos.append((clase, periodo, diferencia))
    return avisos


def tematica(f):
    """(Descripción breve, Idea y temática) con la marca 'provisorio' si corresponde; '—' si falta."""
    marca = " (provisorio)" if f.get("tematica_provisoria") else ""
    def con_marca(x):
        t = _texto(x)
        return t if t == formato.VACIO else t + marca
    return con_marca(f.get("descripcion_breve")), con_marca(f.get("tematica"))


# ───────── Resumen masivo de fondos (pedido 3.1 de octubre) ─────────

# Las 13 categorías resumen, en el orden de la solapa "Resumen" del Excel de carteras.
CATEGORIAS_RESUMEN = ("Disponibilidades", "ON", "Bono corporativo", "Deuda soberana", "Deuda sub-soberana", "Pagaré",
                      "Cheques", "Plazo fijo", "Caución", "Fideicomisos financieros", "FCI", "Acciones",
                      "Otros activos")
ETIQUETA_CATEGORIA = {"Deuda sub-soberana": "Deuda sub-soberana / provincial"}
AJUSTE = "Ajuste a 100% (pasivos/redondeo)"
MONEDA_RESUMEN = {"ARS": "ARS", "USD": "USD", "USB": "USD billete"}
# Las notas al pie de la solapa "Resumen" del Excel (la última se adaptó: el gráfico ahora es el del informe).
NOTAS_RESUMEN = (
    "Patrimonio neto en la moneda de cada fondo (ARS o USD): no es comparable entre filas sin convertir a una misma moneda.",
    "Columnas de tipo de activo: suma de '% del PN' por 'Categoría (resumen)' de cada cartera. Las tenencias 1°-3° salen "
    "del detalle por moneda-indexación (rubro + moneda + indexación).",
    "Sub-soberano = provincias. Indexación de soberanos en pesos: CER = Boncer, Lecer, Discount (DICP) y serie TZX "
    "(TZXO7 asignado CER por nomenclatura). Dual = TXMD9. Dólar linked* = D31M7 (por nomenclatura, a verificar). Los "
    "títulos corporativos se clasifican solo por moneda: la CNV no informa si son tasa fija, variable o ajustables.",
    "'Ajuste a 100%': pasivos no detallados por la CNV (negativo = fondo apalancado) más redondeos.",
    "Gráfico del informe: cada fondo es una porción de igual tamaño (1/N, con N = cantidad de fondos de esa moneda).",
)
COLUMNAS_TENENCIA = (("1° tenencia (rubro / moneda / indexación)", "% 1°"), ("2° tenencia", "% 2°"),
                     ("3° tenencia", "% 3°"))


def _pct(x):
    return None if formato._es_nulo(x) else float(x)


def resumen_masivo(fondos, carteras, sumas, tenencias):
    """La tabla de la solapa "Resumen" del Excel, para los fondos elegidos (una fila por fondo, en el orden de `fondos`).

    fondos:    codigo_cnv, nombre, moneda, clasificacion_cnv, descripcion_breve, tematica, tematica_provisoria
    carteras:  codigo_cnv, fecha_cartera, patrimonio          (la última cartera de cada fondo)
    sumas:     codigo_cnv, categoria_resumen, pct              (suma del % del PN por categoría resumen)
    tenencias: codigo_cnv, posicion (1 a 3), categoria_detallada, pct_pn

    Los porcentajes quedan en puntos (94,44 = 94,44%). Las columnas que empiezan con "_" son para el informe."""
    import pandas as pd
    cart = {int(r["codigo_cnv"]): r for _, r in carteras.iterrows()}
    por_cat = {}
    for _, r in sumas.iterrows():
        por_cat.setdefault(int(r["codigo_cnv"]), {})[r["categoria_resumen"]] = float(r["pct"])
    ten = {}
    for _, r in tenencias.iterrows():
        ten.setdefault(int(r["codigo_cnv"]), {})[int(r["posicion"])] = (r["categoria_detallada"], _pct(r["pct_pn"]))
    filas = []
    for _, f in fondos.iterrows():
        cod = int(f["codigo_cnv"])
        c, cats = cart.get(cod), por_cat.get(cod, {})
        fila = {"Fondo": f["nombre"], "Moneda": MONEDA_RESUMEN.get(f["moneda"], formato.VACIO),
                "Clasificación CNV": _texto(f.get("clasificacion_cnv")),   # solo la de la ficha de la CNV, nunca la de la CAFCI
                "Descripción breve": textos_fondo(f.to_dict())[0],
                "Patrimonio neto": None if c is None else _pct(c["patrimonio"])}
        for cat in CATEGORIAS_RESUMEN:
            fila[ETIQUETA_CATEGORIA.get(cat, cat)] = cats.get(cat, 0.0)
        for extra in ('Sin detalle', 'Futuros', 'sin clasificar'):
            if any(extra in valores for valores in por_cat.values()):
                fila[extra] = cats.get(extra, 0.0)
        fila['_fuente'] = 'cnv' if c is None else c.get('fuente', 'cnv')
        if 'fuente' in carteras and carteras.fuente.eq('cafci').any():
            fila['Fuente'] = 'CAFCI parcial' if fila['_fuente'] == 'cafci' else 'CNV'
        fila[AJUSTE] = (None if fila['_fuente'] == 'cafci' else
                        round(100 - sum(cats.get(cat, 0.0) for cat in CATEGORIAS_RESUMEN), 4))
        for pos, (col, col_pct) in enumerate(COLUMNAS_TENENCIA, start=1):
            nombre, pct = ten.get(cod, {}).get(pos, (formato.VACIO, None))
            fila[col], fila[col_pct] = nombre, pct
        fila["Cartera al"] = None if c is None else c["fecha_cartera"]
        fila["_codigo"], fila["_moneda"] = cod, f["moneda"]
        propios = textos_fondo(f.to_dict())
        fila["_idea"] = propios[1]
        fila["_idea_pdf"] = propios[1] if descripcion_fondo(f.to_dict())[1] else _texto(f.get("tematica"))        # el PDF para clientes no lleva la marca "(provisorio)"
        filas.append(fila)
    return pd.DataFrame(filas)


def columnas_resumen(tabla):
    """Columnas de `resumen_masivo` que se ven en pantalla ('Cartera al' solo si las fechas de cartera difieren)."""
    cols = [c for c in tabla.columns if not c.startswith("_")]
    if tabla["Cartera al"].nunique(dropna=False) <= 1:
        cols.remove("Cartera al")
    return cols


# ───────── Gestión y eficiencia (pedido 3.2 de octubre) ─────────

PESO_RENDIMIENTO = 0.70
PESO_HONORARIO = 0.30
MINIMO_GRUPO = 3
MINIMO_ATIPICOS = 8        # con menos clases en el grupo no se marca ninguna como atípica
FACTOR_VALLA = 3           # entre esta valla y la extrema: entra al ranking con una marca "revisar"
FACTOR_VALLA_EXTREMA = 10  # más allá de esta valla: afuera, error de datos probable
DESCARGO_EFICIENCIA = ("Ranking informativo. No constituye recomendación de inversión. Los rendimientos ya son netos de "
                       "honorarios y gastos; los rendimientos pasados no garantizan rendimientos futuros.")
MOTIVO_HONORARIO_CERO = "Honorario 0% informado, verificar"
MOTIVO_ATIPICO = "Error de datos probable, verificar"
MARCA_MUY_ALTO = "Rendimiento muy alto, revisar"
MARCA_MUY_BAJO = "Rendimiento muy bajo, revisar"
MOTIVO_ESTADO = {"(sin patrimonio)": "Sin patrimonio", "(sin datos recientes)": "Sin datos recientes",
                 "(fuera de la planilla)": "Fuera de la planilla"}


def clases_a_mostrar(clases, todas=False):
    """Por defecto, Clase A y B de cada fondo (si el fondo no las tiene, las que haya); con `todas`, todas las clases."""
    if todas or clases.empty:
        return clases
    ab = clases["clase"].str.contains(r"Clase [AB]\b", regex=True)
    tiene = ab.groupby(clases["codigo_cnv"]).transform("any")
    return clases[ab | ~tiene]


def orden_inicial(clases):
    """De mayor a menor honorario de la sociedad gerente (los sin dato al final); a igualdad, por nombre."""
    return clases.sort_values(["honorarios_sg", "clase"], ascending=[False, True], na_position="last",
                              kind="stable").reset_index(drop=True)


def grupo_moneda(moneda):
    return "Pesos" if moneda in PESOS else "Dólares"


def motivo_exclusion(c):
    """Por qué una clase no entra al ranking (None si entra). Cualquier estado de la foto la deja afuera."""
    estado = c.get("marca_estado")
    if not formato._es_nulo(estado) and str(estado).strip():
        estado = str(estado).strip()
        return MOTIVO_ESTADO.get(estado, f"Con marca {estado} en la planilla (suspendido o en liquidación)")
    if formato._es_nulo(c.get("rend_12m")):
        return "Sin 12 meses de historia"
    if formato._es_nulo(c.get("honorarios_sg")):
        return "Sin honorario de la sociedad gerente informado"
    if formato._es_nulo(c.get("tipo_fondo")):
        return "Sin tipo de fondo"
    return None


def _percentil(serie, mas_alto_es_mejor):
    """0 a 100 dentro del grupo: el peor vale 0 y el mejor 100; los empatados comparten el promedio de sus lugares."""
    n = len(serie)
    p = (serie.rank(method="average") - 1) / (n - 1) * 100       # rank 1 = el valor más bajo
    return p if mas_alto_es_mejor else 100 - p


def ejecutar_eficiencia(clases):
    """Puntaje de eficiencia 0-100 = 70% percentil del rendimiento 12m (más alto, mejor) + 30% percentil del honorario
    de la sociedad gerente (más bajo, mejor), SOLO dentro del mismo tipo de fondo y moneda. Los demás costos no entran.

    clases: DataFrame con tipo_fondo, moneda, rend_12m, honorarios_sg, marca_estado (y lo que se quiera mostrar).
    Devuelve (ranking, afuera):
      ranking: las clases comparables con 'puntaje', 'posicion' (1 = la mejor de su grupo; empatadas comparten lugar),
               'en_grupo' (cuántas clases tiene el grupo), 'grupo' (texto) y 'marca' (rendimiento muy alto o muy bajo, a
               revisar; vacía si no), ordenado por puntaje de mayor a menor.
      afuera:  las demás, con 'motivo' (estado, sin 12 meses, sin honorario, honorario 0%, error de datos probable, o grupo
               con menos de 3 clases). Los percentiles se calculan sin las que quedan afuera."""
    clases = clases.copy().reset_index(drop=True)
    clases["motivo"] = [motivo_exclusion(r) for _, r in clases.iterrows()]
    clases["grupo"] = [f"{t} · {grupo_moneda(m)}" for t, m in zip(clases["tipo_fondo"], clases["moneda"])]
    # Honorario de la sociedad gerente 0%: puede ser un dato faltante de la CAFCI, queda afuera (sigue visible en la tabla).
    cero = clases["motivo"].isna() & (clases["honorarios_sg"] == 0)
    clases.loc[cero, "motivo"] = MOTIVO_HONORARIO_CERO
    # Rendimiento atípico en dos niveles, dentro de cada grupo (IQR = Q3 - Q1):
    #   más allá de Q3 + 10 x IQR o de Q1 - 10 x IQR: afuera (error de datos probable);
    #   entre la valla de 3 x IQR y la de 10 x IQR: entra al ranking con una marca "revisar".
    # Q1 y Q3 se calculan con las clases que siguen en carrera (sin las de honorario 0% ni las de estado). Un grupo de
    # menos de MINIMO_ATIPICOS de esas clases no marca ni saca ninguna.
    en_carrera = clases[clases["motivo"].isna()]
    rend = en_carrera.groupby("grupo")["rend_12m"]
    q1, q3 = rend.transform(lambda s: s.quantile(0.25)), rend.transform(lambda s: s.quantile(0.75))
    iqr = q3 - q1
    r12 = en_carrera["rend_12m"]
    grande = rend.transform("size") >= MINIMO_ATIPICOS
    extrema = ((r12 > q3 + FACTOR_VALLA_EXTREMA * iqr) | (r12 < q1 - FACTOR_VALLA_EXTREMA * iqr)) & grande
    clases.loc[extrema[extrema].index, "motivo"] = MOTIVO_ATIPICO
    clases["marca"] = ""
    alto = (r12 > q3 + FACTOR_VALLA * iqr) & ~extrema & grande
    bajo = (r12 < q1 - FACTOR_VALLA * iqr) & ~extrema & grande
    clases.loc[alto[alto].index, "marca"] = MARCA_MUY_ALTO
    clases.loc[bajo[bajo].index, "marca"] = MARCA_MUY_BAJO
    ok = clases[clases["motivo"].isna()].copy()
    chicos = ok.groupby("grupo")["grupo"].transform("size") < MINIMO_GRUPO
    clases.loc[ok[chicos].index, "motivo"] = f"Grupo con menos de {MINIMO_GRUPO} clases comparables"
    ok = ok[~chicos].copy()
    if ok.empty:
        ok["puntaje"], ok["posicion"], ok["en_grupo"] = [], [], []
    else:
        ok["en_grupo"] = ok.groupby("grupo")["grupo"].transform("size")
        ok["puntaje"] = (PESO_RENDIMIENTO * ok.groupby("grupo")["rend_12m"].transform(lambda s: _percentil(s, True))
                         + PESO_HONORARIO * ok.groupby("grupo")["honorarios_sg"].transform(lambda s: _percentil(s, False)))
        ok["posicion"] = ok.groupby("grupo")["puntaje"].rank(method="min", ascending=False).astype(int)
        ok = ok.sort_values(["puntaje", "honorarios_sg", "clase"], ascending=[False, True, True], kind="stable")
    afuera = clases[clases["motivo"].notna()]
    return ok.drop(columns="motivo").reset_index(drop=True), afuera.reset_index(drop=True)


# ───────── Sumar o quitar fondos (pedidos de la app al robot) ─────────

ACCIONES_PEDIDO = {"seguir": "Seguir", "dejar": "Dejar de seguir", "editar_tematica": "Editar descripción y temática"}
ESTADOS_PEDIDO = {"pendiente": "Pendiente", "listo": "Listo", "problema": "Problema"}
MAX_DESCRIPCION = 500          # mismos topes que la política de la base (base_de_datos/usuario_pedidos.sql)
MAX_IDEA = 2000
MAX_DIGITOS_ID_FICHA = 9


def _sin_tildes(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or "").lower()) if unicodedata.category(c) != "Mn")


def filtrar_catalogo(df, texto="", moneda="Todas", tipos=(), rubros=(), situacion="Todos"):
    """Buscador del catálogo completo. `texto`: todas las palabras tienen que estar en el nombre del fondo o de su gerente
    (sin importar mayúsculas ni tildes). `moneda`: 'Todas', 'Pesos' o 'Dólares'. `situacion`: 'Todos', 'Seguidos' o
    'No seguidos'. Sin tipos o rubros elegidos no se filtra por ellos."""
    out = filtrar_moneda(df, moneda)
    if tipos:
        out = out[out["tipo_fondo"].isin(tipos)]
    if rubros:
        out = out[out["rubro"].isin(rubros)]
    if situacion == "Seguidos":
        out = out[out["seguido"]]
    elif situacion == "No seguidos":
        out = out[~out["seguido"]]
    palabras = _sin_tildes(texto).split()
    if palabras:
        pajar = (out["nombre"].map(_sin_tildes) + " " + out["gerente"].map(_sin_tildes))
        for p in palabras:
            out = out[pajar.loc[out.index].str.contains(p, regex=False)]
    return out.sort_values("nombre", kind="stable")


def codigo_fila_catalogo(codigos, filas):
    """Las posiciones de Streamlit corresponden al orden enviado, incluso al ordenar en el navegador."""
    if len(filas) != 1:
        return None
    posicion = filas[0]
    if not isinstance(posicion, int) or isinstance(posicion, bool) or not 0 <= posicion < len(codigos):
        return None
    return codigos[posicion]


def validar_pedido(accion, descripcion=None, idea=None, id_ficha=None):
    """Limpia y controla lo que se escribió en la pantalla. Devuelve (datos, error): `datos` = {'accion', 'descripcion',
    'idea', 'id_ficha'} (los vacíos quedan en None) y `error` = texto para mostrar, o None si está todo bien."""
    if accion not in ACCIONES_PEDIDO:
        return None, "Acción desconocida."
    texto = lambda x: x.strip() if isinstance(x, str) else ""      # noqa: E731  (un dato vacío de la base llega como None o NaN)
    desc, idea = texto(descripcion) or None, texto(idea) or None
    ficha = (str(id_ficha).strip().replace(".", "") if id_ficha is not None and id_ficha == id_ficha else "") or None
    if accion == "dejar":
        return {"accion": accion, "descripcion": None, "idea": None, "id_ficha": None}, None
    if desc and len(desc) > MAX_DESCRIPCION:
        return None, f"La descripción breve tiene {len(desc)} caracteres: el máximo es {MAX_DESCRIPCION}."
    if idea and len(idea) > MAX_IDEA:
        return None, f"La idea y temática tiene {len(idea)} caracteres: el máximo es {MAX_IDEA}."
    if ficha is not None:
        if not ficha.isdigit() or int(ficha) == 0 or len(ficha) > MAX_DIGITOS_ID_FICHA:
            return None, "El ID de la ficha CNV son solo números (por ejemplo 63491); está en la dirección de la ficha del fondo en la web de la CNV."
        ficha = int(ficha)
    if accion == "editar_tematica":
        ficha = None
    return {"accion": accion, "descripcion": desc, "idea": idea, "id_ficha": ficha}, None


class ErrorSeguimiento(ValueError):
    """Rechazo o fallo explícito; nunca representa una operación aplicada."""


@contextmanager
def medir_seguimiento(etapa):
    """Solo etapa fija y duración; sin SQL, valores ni identidades."""
    inicio = time.perf_counter()
    try:
        yield
    finally:
        logging.getLogger('seguimiento').log(
            logging.WARNING, 'SEGUIMIENTO TIEMPO %s %.3f_ms', etapa, (time.perf_counter() - inicio) * 1000)


def registrar_fallo_seguimiento(etapa, motivo, error):
    """Solo códigos internos y SQLSTATE; nunca mensajes, parámetros ni traceback."""
    estado = getattr(error, 'sqlstate', None)
    estado = estado if isinstance(estado, str) and re.fullmatch(r'[0-9A-Z]{5}', estado) else 'SIN_SQLSTATE'
    tipo = type(error).__name__
    tipo = tipo if re.fullmatch(r'[A-Za-z_]{1,80}', tipo) else 'Error'
    logging.getLogger('seguimiento').log(logging.WARNING, 'SEGUIMIENTO %s %s %s %s', etapa, motivo, estado, tipo)


def _contexto_seguimiento(resolver):
    # Resolver confiable del servidor, no una identidad enviada por el navegador.
    if resolver is None:
        from acceso import requerir_contexto_seguimiento
        resolver = requerir_contexto_seguimiento
    motivo = 'RESOLVER'
    try:
        contexto = resolver()
        for campo in ('organizacion', 'dueno', 'usuario', 'version_clave'):
            motivo = 'CAMPO_' + campo.upper()
            if not isinstance(contexto.get(campo), str) or not contexto[campo].strip():
                raise ValueError()
        motivo = 'ROL'
        if (contexto.get('rol') not in ('admin', 'asesor')
                and not (contexto.get('rol') == 'cliente'
                         and contexto.get('roles_permisos') is True)):
            raise ValueError()
        return contexto
    except Exception as error:
        registrar_fallo_seguimiento('CONTEXTO', motivo, error)
        raise ErrorSeguimiento("CONTEXTO_RECHAZADO") from None


def _par_seguimiento(contexto):
    return contexto["organizacion"], contexto["dueno"]


def _filas_seguimiento(estado, contexto):
    par = _par_seguimiento(contexto)
    for tabla in ("elecciones", "textos", "pedidos"):
        for fila in estado[tabla]:
            if (fila.get("organizacion"), fila.get("dueno")) != par:
                raise ErrorSeguimiento("DUENO_AJENO")
    return estado


def componer_seguimiento(catalogo, repositorio, resolver=None):
    """Lectura privada sin caché; repositorio.leer(par) debe fallar explícitamente."""
    contexto = _contexto_seguimiento(resolver)
    try:
        estado = _filas_seguimiento(repositorio.leer(_par_seguimiento(contexto)), contexto)
        elecciones = {f["codigo_cnv"]: f for f in estado["elecciones"]}
        textos = {f["codigo_cnv"]: f for f in estado["textos"]}
        salida = deepcopy(catalogo)
        for fila in salida:
            codigo = fila["codigo_cnv"]
            fila["seguido"] = elecciones.get(codigo, {}).get("seguido", False)
            for campo in ("descripcion_breve", "tematica", "tematica_provisoria"):
                fila[campo] = deepcopy(textos.get(codigo, {}).get(campo))
        return salida
    except ErrorSeguimiento:
        raise
    except Exception:
        raise ErrorSeguimiento("LECTURA_FALLIDA") from None


def lectura_seguimiento_activa():
    from acceso import lector_seguimiento
    return lector_seguimiento() is not None


def seguimiento_para_consulta(catalogo):
    """Compone un DataFrame fuera de la caché común; sin backend devuelve el original.

    Los errores rechazan la consulta completa: nunca recuperan textos/elecciones
    globales. Conserva índice, columnas y atributos usados por clasificación.
    """
    from acceso import lector_seguimiento
    lector = lector_seguimiento()
    if lector is None:
        return catalogo
    backend, resolver = lector
    filas = componer_seguimiento(catalogo.to_dict('records'), backend, resolver)
    salida = catalogo.copy(deep=True)
    for campo in ('seguido', 'descripcion_breve', 'tematica', 'tematica_provisoria'):
        salida[campo] = pd.Series([fila[campo] for fila in filas], index=salida.index,
                                  dtype=bool if campo == 'seguido' else object)
    return salida


def pedidos_seguimiento(repositorio, resolver=None):
    """Pedidos de todos los autores del par autorizado, sin caché ni filtro personal."""
    contexto = _contexto_seguimiento(resolver)
    try:
        estado = _filas_seguimiento(repositorio.leer(_par_seguimiento(contexto)), contexto)
        return deepcopy(estado["pedidos"])
    except ErrorSeguimiento:
        raise
    except Exception:
        raise ErrorSeguimiento("LECTURA_FALLIDA") from None


def versiones_formulario_seguimiento(codigo, repositorio, resolver):
    contexto = _contexto_seguimiento(resolver)
    try:
        estado = _filas_seguimiento(repositorio.leer(_par_seguimiento(contexto)), contexto)
        return {campo: next((f.get('version', 0) for f in estado[tabla]
                             if f['codigo_cnv'] == codigo), 0)
                for campo, tabla in [('version_esperada', 'elecciones'),
                                     ('version_textos_esperada', 'textos')]}
    except ErrorSeguimiento:
        raise
    except Exception:
        raise ErrorSeguimiento('LECTURA_FALLIDA') from None


def tabla_pedidos_seguimiento(pedidos, catalogo, limite):
    """Adapta el contrato privado a la presentación existente, incluso sin pedidos."""
    nombres = {f['codigo_cnv']: f.get('nombre') for f in catalogo}
    columnas = ['id', 'codigo_cnv', 'fondo', 'accion', 'descripcion_breve', 'idea_tematica',
                'id_ficha_cnv', 'estado', 'motivo', 'pedido_en', 'procesado_en']
    normalizados = []
    for pedido in pedidos:
        legado = pedido.get('pedido_legado') or {}
        p = dict(legado)
        for nuevo, viejo in (('descripcion', 'descripcion_breve'), ('idea', 'idea_tematica'),
                             ('id_ficha', 'id_ficha_cnv')):
            p[nuevo] = legado.get(viejo)
        p.update(pedido)
        p.setdefault('codigo_cnv', None)
        p.setdefault('accion', None)
        normalizados.append(p)
    filas = [dict(id=i, codigo_cnv=p['codigo_cnv'], fondo=nombres.get(p['codigo_cnv']),
                  accion=p['accion'], descripcion_breve=p.get('descripcion'),
                  idea_tematica=p.get('idea'), id_ficha_cnv=p.get('id_ficha'),
                  estado=p['estado'], motivo=p.get('motivo'), pedido_en=p.get('pedido_en'),
                  procesado_en=p.get('procesado_en')) for i, p in enumerate(normalizados, 1)]
    return pd.DataFrame(filas, columns=columnas).tail(limite).iloc[::-1]


def solicitar_seguimiento(catalogo, repositorio, formulario, resolver=None):
    """Anota pendiente, nunca aplicado. transaccion(par, función) debe ser atómica.

    El adaptador futuro deberá bloquear, verificar versión/pendiente y confirmar antes
    de retornar; U37 solo prueba este protocolo con un repositorio ficticio.
    """
    contexto = _contexto_seguimiento(resolver)
    permitidos = {"accion", "codigo_cnv", "version_esperada", "version_textos_esperada",
                  "clave", "descripcion", "idea", "id_ficha"}
    if not isinstance(formulario, dict) or set(formulario) - permitidos:
        raise ErrorSeguimiento("FORMULARIO_INVALIDO")
    accion = formulario.get("accion")
    if accion not in ("seguir", "dejar", "editar_tematica"):
        raise ErrorSeguimiento("ACCION_NO_HABILITADA")
    campos = [k for k in ("descripcion", "idea") if k in formulario]
    if accion == "editar_tematica" and (not campos or "id_ficha" in formulario):
        raise ErrorSeguimiento("FORMULARIO_INVALIDO")
    codigo, version, clave = (formulario.get(k) for k in ("codigo_cnv", "version_esperada", "clave"))
    version_textos = formulario.get("version_textos_esperada")
    if (type(codigo) is not int or codigo <= 0
            or codigo not in {f["codigo_cnv"] for f in catalogo}
            or type(version) is not int or version < 0
            or type(version_textos) is not int or version_textos < 0
            or not isinstance(clave, str) or not clave.strip()):
        raise ErrorSeguimiento("SOLICITUD_INVALIDA")
    if any(formulario.get(k) is not None and not isinstance(formulario[k], str)
           for k in ("descripcion", "idea")):
        raise ErrorSeguimiento("TEXTOS_INVALIDOS")
    datos, error = validar_pedido(accion, formulario.get("descripcion"), formulario.get("idea"),
                                 formulario.get("id_ficha"))
    if error:
        raise ErrorSeguimiento("LIMITES_INVALIDOS")
    intencion = {**datos, "codigo_cnv": codigo, "version_esperada": version,
                 "version_textos_esperada": version_textos,
                 "clave": clave, "usuario": contexto["usuario"],
                 "organizacion": contexto["organizacion"], "dueno": contexto["dueno"]}
    if accion == "editar_tematica":
        intencion["campos_textos"] = campos

    def anotar(estado):
        _filas_seguimiento(estado, contexto)
        if contexto.get('roles_permisos') is True:
            from permisos import permitido
            accion_permiso = {'seguir': 'pedir_seguir', 'dejar': 'pedir_dejar',
                              'editar_tematica': 'editar_texto_propio'}[accion]
            propio = any(p.get('codigo_cnv') == codigo and p.get('accion') == 'seguir'
                         and p.get('usuario') == contexto['usuario'] for p in estado['pedidos'])
            dato = {**contexto, 'usuario': contexto['usuario'] if propio else None}
            if accion == 'editar_tematica' and not propio:
                accion_permiso = 'editar_texto_ajeno'
            if not permitido(contexto['rol'], accion_permiso, dato,
                             contexto['usuario'], contexto['organizacion']):
                # Administrador puede editar incluso un fondo sin autor registrado.
                if not (contexto['rol'] == 'admin' and accion == 'editar_tematica'):
                    raise ErrorSeguimiento('PERMISO_DENEGADO')
            if (contexto['rol'] == 'asesor' and accion == 'seguir' and campos and not propio
                    and any(f['codigo_cnv'] == codigo for f in estado['elecciones'])):
                raise ErrorSeguimiento('PERMISO_DENEGADO')
        for pedido in estado["pedidos"]:
            if pedido["clave"] == clave:
                if any(pedido.get(k) != v for k, v in intencion.items()):
                    raise ErrorSeguimiento("IDEMPOTENCIA_INCOMPATIBLE")
                return deepcopy(pedido)
        actual = next((f for f in estado["elecciones"] if f["codigo_cnv"] == codigo), {})
        if actual.get("version", 0) != version:
            raise ErrorSeguimiento("VERSION_VIEJA")
        texto = next((f for f in estado["textos"] if f["codigo_cnv"] == codigo), {})
        if texto.get("version", 0) != version_textos:
            raise ErrorSeguimiento("VERSION_TEXTOS_VIEJA")
        if any(p["codigo_cnv"] == codigo and p["estado"] == "pendiente" for p in estado["pedidos"]):
            raise ErrorSeguimiento("YA_PENDIENTE")
        pedido = {**intencion, "estado": "pendiente",
                  "auditoria": [{"evento": "solicitado", **deepcopy(intencion)}]}
        estado["pedidos"].append(pedido)
        return deepcopy(pedido)

    try:
        return repositorio.transaccion(_par_seguimiento(contexto), anotar)
    except ErrorSeguimiento:
        raise
    except Exception:
        raise ErrorSeguimiento("REGISTRO_FALLIDO") from None


def confirmar_seguimiento_ficticio(estado, clave, preparado=False):
    """Transición pura del futuro operador; NO es API de formulario ni procesador.

    Retorna copia para commit atómico de elección/textos/estado por el adaptador.
    preparado acredita recursos comunes únicamente en la simulación.
    """
    nuevo = deepcopy(estado)
    pedido = next((p for p in nuevo["pedidos"] if p["clave"] == clave), None)
    if pedido is None or pedido["accion"] not in ("seguir", "dejar", "editar_tematica"):
        raise ErrorSeguimiento("PEDIDO_INVALIDO")
    _filas_seguimiento(nuevo, pedido)
    if pedido["estado"] == "listo":
        return nuevo
    if pedido["estado"] != "pendiente":
        raise ErrorSeguimiento("PEDIDO_INVALIDO")
    codigo = pedido["codigo_cnv"]
    eleccion = next((f for f in nuevo["elecciones"] if f["codigo_cnv"] == codigo), None)
    if (eleccion or {}).get("version", 0) != pedido["version_esperada"]:
        raise ErrorSeguimiento("VERSION_VIEJA")
    texto = next((f for f in nuevo["textos"] if f["codigo_cnv"] == codigo), None)
    if (texto or {}).get("version", 0) != pedido["version_textos_esperada"]:
        raise ErrorSeguimiento("VERSION_TEXTOS_VIEJA")
    if pedido["accion"] == "seguir" and preparado is not True:
        raise ErrorSeguimiento("PREPARACION_INCOMPLETA")
    antes = deepcopy({"eleccion": eleccion, "textos": texto})
    editar = pedido["accion"] == "editar_tematica"
    if not editar:
        if eleccion is None:
            eleccion = {k: pedido[k] for k in ("organizacion", "dueno", "codigo_cnv")}
            nuevo["elecciones"].append(eleccion)
        eleccion.update(seguido=pedido["accion"] == "seguir", version=pedido["version_esperada"] + 1,
                        usuario=pedido["usuario"])
    if editar or (pedido["accion"] == "seguir" and (pedido["descripcion"] or pedido["idea"])):
        if texto is None:
            texto = {k: pedido[k] for k in ("organizacion", "dueno", "codigo_cnv")}
            nuevo["textos"].append(texto)
        for origen, destino in (("descripcion", "descripcion_breve"), ("idea", "tematica")):
            if (editar and origen in pedido["campos_textos"]) or (not editar and pedido[origen]):
                texto[destino] = pedido[origen]
        texto.update(version=texto.get("version", 0) + 1, usuario=pedido["usuario"])
    pedido.setdefault("auditoria", []).append({
        "evento": "confirmado_ficticio",
        **{k: pedido[k] for k in ("organizacion", "dueno", "usuario", "clave", "accion", "codigo_cnv")},
        "antes": antes, "despues": deepcopy({"eleccion": eleccion, "textos": texto})})
    pedido["estado"] = "listo"
    return nuevo


def necesidades_seguimiento(elecciones, reservas, pares_habilitados):
    """Unión común pura del operador, no accesible desde formularios privados.

    Reservas operativas explícitas: codigo_cnv, activa (bool), motivo no vacío.
    No pausa descargas, no borra datos; el bloqueo/reconciliación real queda pendiente.
    """
    necesidad = set()
    for fila in elecciones:
        if ((fila.get("organizacion"), fila.get("dueno")) not in pares_habilitados
                or type(fila.get("seguido")) is not bool):
            raise ErrorSeguimiento("ELECCION_INVALIDA")
        if fila["seguido"]:
            necesidad.add(fila["codigo_cnv"])
    for fila in reservas:
        if type(fila.get("activa")) is not bool or not isinstance(fila.get("motivo"), str) or not fila["motivo"].strip():
            raise ErrorSeguimiento("RESERVA_INVALIDA")
        if fila["activa"]:
            necesidad.add(fila["codigo_cnv"])
    return necesidad


def motivo_pide_id(motivo):
    """¿El problema se arregla cargando el ID de la ficha CNV? (no se encontró la ficha, no sirve o es de otra gerente)"""
    return "ficha cnv" in str(motivo or "").lower()


def preparar_pedidos(df):
    """Lista de pedidos para mostrar: agrega 'accion_texto', 'estado_texto' (con "reintentado" si después se pidió de
    nuevo lo mismo) y 'reintentable' (un 'seguir' con problema por falta de ID que nadie volvió a pedir).
    Ordenados del más nuevo al más viejo."""
    df = df.sort_values("id", ascending=False).copy()
    df["accion_texto"] = df["accion"].map(ACCIONES_PEDIDO).fillna(df["accion"])
    # Repetido = existe un pedido posterior (id mayor) del mismo fondo y la misma acción; lineal en n.
    repetido = (df.groupby(["codigo_cnv", "accion"])["id"].transform("max") > df["id"]).astype(bool)
    problema = df["estado"] == "problema"
    df["reintentable"] = (df["accion"] == "seguir") & problema & df["motivo"].map(motivo_pide_id) & ~repetido
    df["estado_texto"] = df["estado"].map(ESTADOS_PEDIDO).fillna(df["estado"])
    df.loc[problema & repetido, "estado_texto"] = "Problema (se volvió a pedir)"
    return df


def costos_ficha(tabla):
    """Costos y calificación disponibles de CAFCI/catálogo, formateados para pantalla y PDF."""
    etiquetas = {"Clase": "Clase", "calificacion": "Calificación", "marca_estado": "Estado CAFCI",
        "honorarios_sg": "Honorarios gerente %", "honorarios_sd": "Honorarios depositaria %",
        "gastos_ordinarios": "Gastos ordinarios %", "comision_ingreso": "Ingreso %",
        "comision_rescate": "Rescate %", "comision_transferencia": "Transferencia %",
        "honorarios_exito": "Éxito %"}
    salida = tabla[[c for c in etiquetas if c in tabla]].rename(columns=etiquetas)
    return formato.tabla(salida, numeros={c: 4 for c in salida if c.endswith("%")}).fillna(formato.VACIO)


# ───────── U126: solapamiento de carteras entre fondos ─────────

MAX_FONDOS_SOLAPAMIENTO = 6
DIAS_FECHAS_CERCANAS = 7
DIAS_FECHAS_LEJANAS = 35
_NO_ACTIVO = {"Pasivos", "Sin detalle", "Disponibilidades"}
_TIPOS_EMISOR = r"(?:ON|O\.N\.|OBLIGACIONES? NEGOCIABLES?|FF|FIDEICOMISO FINANCIERO|VRD|VDF|CP|BONO|BO|TITULO|TIT|LETRA|PAGARE|CHEQUES?)"
_CORTE_EMISOR = re.compile(
    r"\s+(?:C\d+\b|CL\b|CLASE\b|CLASES\b|SERIE\b|S\d+\b|SER\b|REG\b|VTO\b|VENC|TV\b|TF\b|\d|\(|-)", re.I)
_ALIAS_EMISOR = (("BCO ", "BANCO "), ("BBVA FRANCES", "BANCO FRANCES"), ("BANCO BBVA", "BANCO FRANCES"),
                 ("S.A.", ""), (" SA", ""))


def _sin_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")


def clave_activo(instrumento):
    """Identidad de una especie: minúsculas, sin acentos ni espacios repetidos."""
    return re.sub(r"\s+", " ", _sin_acentos(instrumento).casefold()).strip()


@lru_cache(maxsize=8192)
def emisor_de(instrumento, categoria_resumen=None):
    """Emisor aproximado a partir del nombre de la especie (la fuente no trae emisor).

    Quita el tipo inicial ("ON", "Bono"...) y corta en clase/serie/vencimiento; une alias comunes."""
    if categoria_resumen == "Deuda soberana":
        return "Tesoro Nacional (deuda soberana)"
    texto = re.sub(r"\s+", " ", _sin_acentos(instrumento)).strip().upper()
    texto = re.sub(r"^" + _TIPOS_EMISOR + r"\b\.?\s+", "", texto)
    texto = _CORTE_EMISOR.split(texto, maxsplit=1)[0]
    for viejo, nuevo in _ALIAS_EMISOR:
        texto = texto.replace(viejo, nuevo)
    texto = re.sub(r"\s+", " ", texto).strip(" .,")
    return texto.title() if texto else str(instrumento).strip()


def _cartera_solapamiento(fondo):
    """Especies con peso positivo (una fila por línea) y el Resto aparte."""
    filas = fondo["cartera"].copy()
    filas["pct_pn"] = pd.to_numeric(filas["pct_pn"], errors="coerce")
    resto = float(filas.loc[filas["categoria_resumen"].eq("Sin detalle"), "pct_pn"].sum())
    activos = filas[~filas["categoria_resumen"].isin(_NO_ACTIVO) & filas["pct_pn"].gt(0)].copy()
    activos["clave"] = activos["instrumento"].map(clave_activo)
    activos["emisor"] = [emisor_de(i, c) for i, c in zip(activos["instrumento"], activos["categoria_resumen"])]
    activos["clave_emisor"] = activos["emisor"].map(clave_activo)
    return activos, resto


def _minimo_comun(a, b):
    comunes = a.index.intersection(b.index)
    return float(pd.concat([a[comunes], b[comunes]], axis=1).min(axis=1).sum()) if len(comunes) else 0.0


def solapamiento_carteras(fondos):
    """Compara 2 a 6 carteras. `fondos`: lista de dict(codigo_cnv, nombre, fecha_cartera, fuente, cartera).

    Pesos sobre el patrimonio de cada fondo, sin renormalizar. El Resto («Sin detalle») es desconocido:
    no cuenta como coincidencia ni como diferencia. Disponibilidades y pasivos tampoco son especies.
    Devuelve dict con tablas y avisos; con menos de 2 fondos con cartera, solo `avisos`."""
    validos = [f for f in fondos if f.get("cartera") is not None and not f["cartera"].empty]
    avisos = [f"{f['nombre']}: sin cartera cargada; no entra en la comparación." for f in fondos if f not in validos]
    if len(validos) < 2:
        return {"avisos": ["Elegí al menos dos fondos con cartera para comparar."] + avisos, "fondos": []}
    if len(validos) > MAX_FONDOS_SOLAPAMIENTO:
        avisos.append(f"Se comparan como máximo {MAX_FONDOS_SOLAPAMIENTO} fondos; se usan los primeros.")
        validos = validos[:MAX_FONDOS_SOLAPAMIENTO]
    nombres = [f["nombre"] for f in validos]
    activos, restos = {}, {}
    for f in validos:
        activos[f["nombre"]], restos[f["nombre"]] = _cartera_solapamiento(f)
    pesos = {n: a.groupby("clave")["pct_pn"].sum() for n, a in activos.items()}
    emisores = {n: a.groupby("clave_emisor")["pct_pn"].sum() for n, a in activos.items()}
    nombre_activo, nombre_emisor = {}, {}
    for a in activos.values():
        for c, i, e, ce in zip(a["clave"], a["instrumento"], a["emisor"], a["clave_emisor"]):
            nombre_activo.setdefault(c, (i, e))
            nombre_emisor.setdefault(ce, e)

    def compartidos(por_fondo, rotulos, titulos):
        base = pd.concat(por_fondo, axis=1, keys=nombres)
        base = base[base.notna().sum(axis=1) >= 2].copy()
        base["Fondos"] = base[nombres].notna().sum(axis=1)
        base["En común (mín.)"] = base[nombres].min(axis=1, skipna=True)
        base = base.sort_values(["Fondos", "En común (mín.)"], ascending=False)
        for lugar, titulo in enumerate(titulos):
            base.insert(lugar, titulo, [rotulos[c][lugar] if isinstance(rotulos[c], tuple) else rotulos[c] for c in base.index])
        return base.reset_index(drop=True)

    exclusivos = {}
    for n in nombres:
        otros = set().union(*(set(pesos[o].index) for o in nombres if o != n))
        propios = activos[n][~activos[n]["clave"].isin(otros)]
        propios = propios.groupby("clave", as_index=False).agg(
            Activo=("instrumento", "first"), Emisor=("emisor", "first"), **{"% del patrimonio": ("pct_pn", "sum")})
        exclusivos[n] = propios.drop(columns="clave").sort_values("% del patrimonio", ascending=False).reset_index(drop=True)
    conocido = {n: float(pesos[n].sum()) for n in nombres}
    matriz_activos = pd.DataFrame(0.0, index=nombres, columns=nombres)
    matriz_emisores = matriz_activos.copy()
    for i, a in enumerate(nombres):
        matriz_activos.loc[a, a] = matriz_emisores.loc[a, a] = conocido[a]
        for b in nombres[i + 1:]:
            matriz_activos.loc[a, b] = matriz_activos.loc[b, a] = _minimo_comun(pesos[a], pesos[b])
            matriz_emisores.loc[a, b] = matriz_emisores.loc[b, a] = _minimo_comun(emisores[a], emisores[b])
    resumen = pd.DataFrame({"Fondo": nombres, "Cartera al": [f["fecha_cartera"] for f in validos],
                            "Fuente": [f.get("fuente") or "cnv" for f in validos],
                            "Conocido %": [conocido[n] for n in nombres], "Resto %": [restos[n] for n in nombres]})
    dimensiones = {}
    for campo, titulo in (("categoria_resumen", "Tipo de activo"), ("moneda", "Moneda"), ("indexacion", "Indexación")):
        partes = {n: a.assign(**{campo: a[campo].fillna("Sin dato")}).groupby(campo)["pct_pn"].sum()
                  for n, a in activos.items()}
        t = pd.concat(partes, axis=1, keys=nombres).fillna(0.0)
        t.index.name = titulo
        dimensiones[titulo] = t.sort_values(nombres[0], ascending=False).reset_index()
    fechas = [pd.Timestamp(f["fecha_cartera"]) for f in validos if f.get("fecha_cartera") is not None]
    dias = (max(fechas) - min(fechas)).days if fechas else 0
    if dias > DIAS_FECHAS_LEJANAS:
        avisos.append(f"Las carteras están separadas por {dias} días: no son comparables; los resultados pueden engañar.")
    elif len(set(fechas)) > 1:
        avisos.append(f"Las carteras son de fechas distintas ({dias} días entre la más vieja y la más nueva); revisá la fecha de cada fondo.")
    con_resto = [n for n in nombres if restos[n] > 0.005]
    if con_resto:
        avisos.append("Resto (sin detalle, desconocido) en " + ", ".join(f"{n} {restos[n]:.1f}%" for n in con_resto)
                      + ": no cuenta como coincidencia ni como diferencia; podría haber más coincidencias dentro del Resto. "
                        "Una baja coincidencia conocida no prueba que no haya coincidencias ocultas, y un gerente distinto "
                        "no prueba carteras distintas.")
    if len({f.get("fuente") or "cnv" for f in validos}) > 1:
        avisos.append("Las carteras vienen de fuentes distintas (CNV completa y CAFCI parcial): los nombres de las "
                      "especies pueden diferir y ocultar coincidencias; mirá también la coincidencia por emisor.")
    return {"avisos": avisos, "fondos": nombres, "resumen": resumen,
            "compartidos": compartidos(pesos, nombre_activo, ("Activo", "Emisor")),
            "compartidos_emisor": compartidos(emisores, nombre_emisor, ("Emisor",)),
            "exclusivos": exclusivos, "matriz_activos": matriz_activos, "matriz_emisores": matriz_emisores,
            "dimensiones": dimensiones, "dias_entre_fechas": dias}
