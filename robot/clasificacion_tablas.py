"""Fuentes públicas congeladas; índices por proceso, sin red ni base de datos."""
import csv
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
STOP = set('on sa sau de del la el banco cia compania argentina the obligaciones negociables energia energy bc'.split())
ROM = dict(i=1, v=5, x=10, l=50, c=100)


def normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto or '')
                   if unicodedata.category(c) != 'Mn').lower().strip()


def palabras(texto):
    return [p for p in re.findall(r'[a-z0-9]+', normalizar(texto))
            if len(p) > 2 and not p.isdigit() and p not in STOP]


def clase(texto):
    t = normalizar(texto)
    m = re.search(r'\b(?!s\s*\.\s*a\b)(?:clase|cl|c|serie|s)\s*\.?\s*([ivxlc]+|\d{1,3}|[a-z])\b', t)
    if not m:
        m = re.search(r'\s(\d{1,3})\s*$', t)
    if not m:
        return None
    v = m[1]
    if v.isdigit():
        return str(int(v))
    if all(c in ROM for c in v):
        return str(sum(-ROM[a] if b in ROM and ROM[b] > ROM[a] else ROM[a]
                       for a, b in zip(v, v[1:] + ' ')))
    return v.upper() if len(v) == 1 else None


def traducir(fila):
    moneda = {'MEP': 'USD', 'CCL': 'USD', 'USD': 'USD', 'ARS': 'ARS'}.get(fila['Mon. Pago'])
    tasa = normalizar(fila['Clase'])
    indexacion = next((v for k, v in [('dual', 'dual'), ('dolar linked', 'dólar linked'), ('tamar', 'TAMAR'),
                        ('cer', 'CER'), ('uva', 'UVA'), ('badlar', 'BADLAR')]
                       if k in tasa), None)
    if tasa in ('tasa fija', 'step up', 'capitalizable'):
        indexacion = 'hard dollar' if moneda == 'USD' else 'tasa fija' if moneda == 'ARS' else None
    return dict(ticker=fila['Ticker'], categoria={'Corporativo': 'ON', 'Soberano': 'Deuda soberana',
                'Banco central': 'Deuda soberana', 'Provincial': 'Deuda sub-soberana'}.get(fila['Tipo']),
                moneda=moneda, indexacion=indexacion,
                ley={'Nacional': 'ARG', 'Extranjera': 'NY/ext'}.get(fila['Ley']),
                fuente='Screenermatic', fecha_fuente='26/10/2026', regla=9002)


def construir(iamc, screener):
    ticks = defaultdict(list)
    emisores = defaultdict(dict)
    for fila in iamc:
        # USD/ARS son unidades de moneda: el informe también las contiene como encabezados.
        if fila['ticker'].upper() in ('USD', 'ARS', 'CER', 'UVA', 'MEP', 'CCL'):
            continue
        ticks[fila['ticker'].upper()].append(dict(fila, regla=9001))
    for fila in screener:
        dato = traducir(fila)
        if not dato['categoria']:
            continue
        ticks[dato['ticker'].upper()].append(dato)
        numero = clase(fila['Descripción'])
        if numero is None:
            continue
        for palabra in palabras(fila['Emisor'])[:2] + palabras(fila['Descripción'].split(' - ')[0])[:1]:
            identidad = (normalizar(fila['Descripción']), fila['ISIN'], fila['F. Vencimiento'],
                         dato['moneda'], dato['indexacion'], dato['ley'])
            emisores[(palabra, numero)].setdefault(identidad, []).append(dato)
    return ticks, emisores


@lru_cache(maxsize=1)
def cargar():
    with (RAIZ / 'config/clasificacion_iamc.csv').open(encoding='utf-8-sig', newline='') as f:
        iamc = list(csv.DictReader(f, delimiter=';'))
    with (RAIZ / 'config/clasificacion_screenermatic.csv').open(encoding='utf-8-sig', newline='') as f:
        screener = list(csv.DictReader(f))
    return construir(iamc, screener)


def buscar(texto, indices=None):
    ticks, emisores = cargar() if indices is None else indices
    candidatos = [r for t in set(re.findall(r'\b[A-Z0-9]+\b', texto.upper())) for r in ticks.get(t, ())]
    # IAMC primero; varios títulos distintos en un nombre no permiten elegir uno.
    if candidatos:
        if len({r['ticker'] for r in candidatos}) != 1:
            return None
        return min(candidatos, key=lambda r: r['regla'])
    numero = clase(texto)
    if numero is None:
        return None
    candidatos = {}
    for palabra in palabras(texto)[:2]:
        candidatos.update(emisores.get((palabra, numero), {}))
    if len(candidatos) != 1:
        return None
    variantes = next(iter(candidatos.values()))
    # El ticker encontrado por emisor también puede estar en IAMC.
    return min((r for dato in variantes for r in ticks[dato['ticker'].upper()]),
               key=lambda r: r['regla'])


def completar(base, detalle, rubro, contadores=None):
    """Enriquece solo la fuente común; las ediciones privadas se aplican después."""
    dato = buscar(detalle)
    if dato is None:
        if base['categoria_resumen'] == 'sin clasificar' and re.match(r'^fci\s', normalizar(detalle)):
            return dict(base, categoria_resumen='FCI', categoria_detallada='FCI', regla=9003)
        return base
    moneda_cnv = 'USD' if 'dolar' in normalizar(rubro) else 'ARS' if 'peso' in normalizar(rubro) else None
    moneda = (base.get('moneda') or moneda_cnv or '').split(' ')[0]
    if dato.get('moneda') and moneda and dato['moneda'] != moneda:
        if contadores is not None:
            contadores['conflictos_moneda'] = contadores.get('conflictos_moneda', 0) + 1
        return base
    salida = dict(base)
    if base['categoria_resumen'] == 'sin clasificar':
        salida['categoria_resumen'] = {'Soberano': 'Deuda soberana', 'Sub-soberano': 'Deuda sub-soberana'}.get(
            dato['categoria'], dato['categoria'])
        salida['categoria_detallada'] = salida['categoria_resumen']
    agregados = []
    for campo in ('moneda', 'indexacion'):
        if not salida.get(campo) and dato.get(campo):
            salida[campo] = dato[campo]
            agregados.append(dato[campo])
    detallada = salida['categoria_detallada']
    for valor in agregados:
        if normalizar(valor) not in normalizar(detallada):
            detallada += ' ' + valor
    if dato.get('ley') and not re.search(r'\bley\b', normalizar(detallada)):
        detallada += ' ley ' + dato['ley']
    salida.update(categoria_detallada=detallada, regla=dato['regla'])
    if contadores is not None:
        contadores[dato['fuente']] = contadores.get(dato['fuente'], 0) + 1
    return salida
