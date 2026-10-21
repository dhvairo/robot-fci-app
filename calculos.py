"""Lógica de las pantallas que se puede probar sin Streamlit ni base: filtros, períodos y cabecera de la ficha."""
import unicodedata
import json
import logging
import re
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta

import pandas as pd

import formato


# U25: contratos de datos; no asignan UUID, consultan repositorios ni escriben.
CAMPOS_CLASIFICACION = ("categoria_resumen", "categoria_detallada", "moneda", "indexacion")
CAMPOS_IDENTIDAD = ("codigo_cnv", "rubro_cnv_original", "instrumento_original",
                    "moneda_del_fondo_original", "serie", "vencimiento")


class ErrorClasificacion(ValueError):
    """Código fijo, sin contenido de las entradas."""


def preferencias_usuario(sesion, *, cambios=None, restablecer=False):
    """Lectura única por sesión/identidad; guardar actualiza la copia tras commit."""
    from acceso import lector_preferencias
    from robot.preferencias_usuario import Preferencias, efectivas, ErrorPreferencia
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
        from robot.preferencias_usuario import plan_guardado
        if cambios is not None:
            plan_guardado(contexto, cambios)
        cache = sesion.get('preferencias_usuario_cache')
        if cache is None or cache['identidad'] != identidad:
            # Conservar presencia de claves: un default no es una preferencia guardada.
            cache = {'identidad': identidad, 'guardadas': backend.leer(identidad)}
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
    from robot.preferencias_usuario import ErrorPreferencia
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


def opciones_clasificacion(filas_fuente, reglas_organizacion, contexto):
    """Tuplas completas únicas: fuente clasificada y valor privado vigente propio.

    Cada regla contiene fuente_base (dict), parche, instrumento (clave completa),
    organizacion, dueno, activa y version. No se mezclan etiquetas individuales.
    """
    opciones = set()
    for fila in filas_fuente:
        tupla = _tupla_clasificacion(fila)
        if tupla:
            opciones.add(tupla)
    for regla in _reglas_propias(reglas_organizacion, contexto):
        tupla = _tupla_clasificacion(_aplicar_parche(regla.get("fuente_base") or {}, regla["parche"]))
        if tupla:
            opciones.add(tupla)
        # Una regla parcial ya guardada hereda la fuente vigente. Su resultado
        # sigue siendo una opción privada propia aunque cambie un campo heredado.
        for fila in filas_fuente:
            try:
                clave = json.dumps(_componentes(fila), ensure_ascii=False, separators=(',', ':'))
            except ErrorClasificacion:
                continue
            if clave == regla['instrumento']:
                vigente = _tupla_clasificacion(_aplicar_parche(fila, regla['parche']))
                if vigente:
                    opciones.add(vigente)
    return tuple(sorted(opciones, key=lambda t: json.dumps(t, ensure_ascii=False)))


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
    opciones = opciones_clasificacion(
        filas_fuente + [f['fuente'] for f in snapshot['fuentes']], propias, contexto)
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
        try:
            clave = _componentes(fila)
        except ErrorClasificacion:
            clave = None
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
        serializada = json.dumps(clave, ensure_ascii=False, separators=(',', ':'))
        reglas = [r for r in propias if r['instrumento'] == serializada] if registro else []
        if len(reglas) > 1:
            raise ErrorClasificacion('REGLAS_AMBIGUAS')
        regla = reglas[0] if reglas else None
        efectiva = (validar_edicion(fila, regla['parche'], opciones) if regla else
                    {k: deepcopy(fila.get(k)) for k in CAMPOS_CLASIFICACION})
        # La marca fuente explícita también forma parte del contrato U25.
        efectiva['sin_clasificar'] = (_tupla_clasificacion(efectiva) is None if regla
                                     else _tupla_clasificacion(fila) is None)
        pendiente = novedad_fuente(fila, regla, snapshot['decisiones'], contexto)
        if pendiente:
            pendientes.add(pendiente['clave_decision'])
        salida = {**deepcopy(fila), 'efectiva': efectiva, 'identidad': identidad,
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
    return pd.DataFrame([{**deepcopy(f), **deepcopy(f['efectiva'])} for f in filas])


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


def enviar_al_informe(sesion, codigos):
    sesion[SELECCION_INFORME] = list(dict.fromkeys(int(c) for c in codigos))


def historia_comparador(fondos, clases, leer_serie, cargar_puntual):
    """Fuentes inyectables; un fondo sin historia no interrumpe a los demás."""
    partes, avisos = [], []
    for fondo in fondos.to_dict("records"):
        codigos = clases.loc[clases.codigo_cnv.eq(fondo["codigo_cnv"]), "codigo_cafci"]
        if codigos.empty:
            continue
        if fondo["seguido"]:
            historia = leer_serie(codigos)
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
             **memoria[int(c.codigo_cafci)]} for c in clases.itertuples()]


def alternar_serie(sesion, codigo):
    estado = sesion["comparador_series"][codigo]
    estado["visible"] = not estado["visible"]


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
    codigos = monedas_de(opcion)
    return df if codigos is None else df[df[columna].isin(codigos)]


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
        fuentes.append(f"CNV (cartera al {formato.fecha(f['fecha_cartera'])})")
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
                "Descripción breve": _texto(f.get("descripcion_breve")),
                "Patrimonio neto": None if c is None else _pct(c["patrimonio"])}
        for cat in CATEGORIAS_RESUMEN:
            fila[ETIQUETA_CATEGORIA.get(cat, cat)] = cats.get(cat, 0.0)
        fila[AJUSTE] = round(100 - sum(cats.get(cat, 0.0) for cat in CATEGORIAS_RESUMEN), 4)
        for pos, (col, col_pct) in enumerate(COLUMNAS_TENENCIA, start=1):
            nombre, pct = ten.get(cod, {}).get(pos, (formato.VACIO, None))
            fila[col], fila[col_pct] = nombre, pct
        fila["Cartera al"] = None if c is None else c["fecha_cartera"]
        fila["_codigo"], fila["_moneda"] = cod, f["moneda"]
        fila["_idea"] = tematica(f.to_dict())[1]
        fila["_idea_pdf"] = _texto(f.get("tematica"))        # el PDF para clientes no lleva la marca "(provisorio)"
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
    logging.getLogger('seguimiento').log(logging.WARNING, 'SEGUIMIENTO %s %s %s', etapa, motivo, estado)


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
    repetido = pd.Series([bool(((df["codigo_cnv"] == p.codigo_cnv) & (df["accion"] == p.accion) & (df["id"] > p.id)).any())
                          for p in df.itertuples()], index=df.index, dtype=bool)
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
