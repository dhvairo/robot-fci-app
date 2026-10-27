"""Funciones puras del robot distribuidas con la app; test_consulta_puntual verifica igualdad.

Al cambiar el robot, regenerar las funciones correspondientes antes de publicar.
No incluye ejecutores, conexiones ni escritura de archivos.
"""
from robot.clasificacion_tablas import completar
import csv
import html
import html as _html
import json
import re
import unicodedata
from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

RUTA_REGLAS = Path(__file__).with_name("reglas_clasificacion.csv")
EQUIVALENCIAS_GERENTES = json.loads(
    Path(__file__).with_name("equivalencias_gerentes.json").read_text(encoding="utf-8"))
SIN_CLASIFICAR = "sin clasificar"
NO_CUENTAN = {"Pasivos", SIN_CLASIFICAR, "Sin detalle"}
TOLERANCIA_CAFCI = Decimal("0.01")
MAX_HUECO_DIAS = 10
CAMPOS = ["dias_7", "dias_30", "mes", "anio", "meses_12"]

class FichaInvalida(Exception):
    """Ficha ausente o ilegible."""

# Fuente: robot/leer_cartera_cnv.py
def numero_ar(texto):
    """'66.564.606,50' -> 66564606.5 ; '' -> None."""
    t = (texto or "").strip().replace(".", "").replace(",", ".")
    return float(t) if t else None

def _limpiar(fragmento):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragmento))).strip()

def leer_cartera(texto_html):
    """Convierte el HTML de la ficha en un diccionario con la cartera.

    Claves: fondo, fecha_cartera (date), moneda, patrimonio, total_pct, filas.
    Cada fila: rubro, nivel, detalle, pct_pn (instrumento), pct_rubro (rubro).
    """
    ini = texto_html.find('id="1b"')
    if ini < 0:
        raise FichaInvalida("no se encontró la pestaña de composición de cartera")
    seccion = texto_html[ini:]

    m = re.search(r"Fondo</td>\s*<td[^>]*>(.*?)</td>", seccion, flags=re.S)
    fondo = _limpiar(m.group(1)) if m else None
    m = re.search(r"Cartera al \(\*\)</td>\s*<td[^>]*>(.*?)</td>", seccion, flags=re.S)
    if not m:
        raise FichaInvalida("no se encontró la fecha de la cartera")
    try:
        fecha = datetime.strptime(_limpiar(m.group(1)), "%d/%m/%y").date()
    except ValueError:
        raise FichaInvalida(f"fecha de cartera ilegible: {_limpiar(m.group(1))!r}")

    ini_tabla = seccion.find("Detalle (1)")
    if ini_tabla < 0:
        raise FichaInvalida("no se encontró la tabla de cartera")
    tabla = seccion[ini_tabla:seccion.find("</table>", ini_tabla)]

    filas, rubro, total, patrimonio, moneda = [], None, None, None, None
    for tr in re.findall(r"<tr>(.*?)</tr>", tabla, flags=re.S):
        celdas = re.findall(r"<td([^>]*)>(.*?)</td>", tr, flags=re.S)
        if len(celdas) < 3:
            continue
        detalle = _limpiar(celdas[0][1])
        c2, c3 = _limpiar(celdas[1][1]), _limpiar(celdas[2][1])
        if not detalle:
            total = numero_ar(c3)          # fila del total (100,00)
            continue
        if re.fullmatch(r"\(.*\)", detalle):
            moneda, patrimonio = detalle.strip("()"), numero_ar(c2)
            continue
        pad = re.search(r"padding-left:\s*(\d+)px", celdas[0][0])
        nivel = (int(pad.group(1)) // 30) if pad else 0
        if nivel == 0:
            rubro = detalle
        filas.append({"rubro": rubro, "nivel": nivel, "detalle": detalle,
                      "pct_pn": numero_ar(c2), "pct_rubro": numero_ar(c3)})
    if not filas:
        raise FichaInvalida("la tabla de cartera está vacía")
    return {"fondo": fondo, "fecha_cartera": fecha, "moneda": moneda,
            "patrimonio": patrimonio, "total_pct": total, "filas": filas}

def leer_clasificacion(texto_html):
    """Clasificación del fondo que informa la ficha de la CNV ("Renta Fija", "Plazo Fijo"...).

    Devuelve None si la ficha no la informa (así la app no muestra un dato inventado)."""
    m = re.search(r"<td>\s*Clasificaci\S{1,6}n\s*</td>\s*<td[^>]*>(.*?)</td>", texto_html, flags=re.S)
    return (_limpiar(m.group(1)) or None) if m else None

# Fuente: robot/ficha_cnv.py
ALIAS_NOMBRES = json.loads(
    Path(__file__).with_name("alias_nombres_fondos.json").read_text(encoding="utf-8"))


def normalizar_nombre(texto):
    """'GALILEO PREMIUM FCI', 'Galileo Premium' y 'Gainvest FF Ex GAINVEST FIDE...' se comparan sin tildes ni relleno."""
    t = _html.unescape(texto or "").replace("\xa0", " ")
    try:
        t = t.encode("cp1252").decode("utf-8")        # algunas fichas de la CNV traen el texto codificado dos veces
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\bex\b.*$", "", t)                    # "... Ex NOMBRE VIEJO"
    t = re.sub(r"\bf\.?\s?c\.?\s?i\.?\b", " ", t)      # FCI, F.C.I., FC.I.
    t = re.sub(r"fondos? comun(?:es)? de inversion", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

def leer_lista(texto_html):
    ini = texto_html.find('name="Lfc"')
    if ini < 0:
        raise FichaInvalida("no se encontró la lista de fondos en el buscador de la CNV")
    seleccion = texto_html[ini:texto_html.find("</select>", ini)]
    fondos = [(int(v), _html.unescape(n).strip()) for v, n in re.findall(r'<option value="(\d+)">(.*?)</option>',
                                                                           seleccion, flags=re.S)]
    if len(fondos) < 100:
        raise FichaInvalida(f"la lista de fondos de la CNV tiene solo {len(fondos)} fondos: algo cambió en la página")
    return fondos

def buscar_id(nombre, lista):
    """Devuelve (id_ficha, None) si hay UN solo fondo con ese nombre; si no, (None, motivo)."""
    clave = normalizar_nombre(nombre)
    clave = next((normalizar_nombre(destino) for origen, destino in ALIAS_NOMBRES.items()
                  if normalizar_nombre(origen) == clave), clave)
    coinciden = [(i, n) for i, n in lista if clave and normalizar_nombre(n) == clave]
    if len(coinciden) == 1:
        return coinciden[0][0], None
    if not coinciden:
        return None, f"no hay un fondo llamado '{nombre}' en la lista de la CNV"
    return None, (f"hay {len(coinciden)} fichas con el nombre '{nombre}' en la CNV "
                  f"({', '.join(str(i) for i, _ in coinciden)})")

def gerente_de_ficha(texto_html):
    """Sociedad gerente que informa la ficha (None si no la encuentra)."""
    m = re.search(r"<td>\s*Gerente\s*</td>\s*<td[^>]*>(.*?)</td>", texto_html, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else None

def misma_gerente(a, b):
    """Dos nombres de sociedad gerente se consideran iguales si empiezan con la misma palabra (la marca) y las palabras
    de uno están todas en el otro (sin tildes, sin "S.A.", "SAU" ni "Sociedad Gerente de Fondos Comunes de Inversión").
    Solo las marcas de la tabla de configuración se consideran equivalentes."""
    def palabras(t):
        t = normalizar_nombre(t)
        return [w for w in t.split() if w not in ("s", "a", "u", "sa", "sau")]
    x, y = palabras(a or ""), palabras(b or "")
    for grupo in EQUIVALENCIAS_GERENTES:
        marcas = [normalizar_nombre(m) for m in grupo]
        for p in (x, y):
            if p and p[0] in marcas:
                p[0] = marcas[0]
    return bool(x) and bool(y) and x[0] == y[0] and (set(x) <= set(y) or set(y) <= set(x))

# Fuente: robot/clasificar.py
def normalizar(texto):
    """Minúsculas y sin acentos: 'Dólar' -> 'dolar'."""
    t = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower().strip()

def cargar_reglas(ruta=RUTA_REGLAS):
    """Lee el CSV de reglas (acepta ; o ,) y las devuelve ordenadas."""
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        lineas = f.read().splitlines()
    delim = ";" if ";" in lineas[0] else ","
    reglas = []
    for r in csv.DictReader(lineas, delimiter=delim, restkey="_sobran"):
        if not (r.get("orden") or "").strip():
            continue
        if r.get("_sobran") or any(v is None for k, v in r.items() if k != "_sobran"):
            raise ValueError(f"Regla {r.get('orden')}: el número de columnas no es el esperado "
                             f"(¿hay un '{delim}' dentro de un texto?). Revisá esa línea de {Path(ruta).name}.")
        r = {k: (v or "").strip() for k, v in r.items()}
        r["orden"] = int(r["orden"])
        try:
            r["_re"] = re.compile(normalizar(r["patron"])) if r["patron"] else None
        except re.error as e:
            raise ValueError(f"Regla {r['orden']}: patrón inválido ({e})")
        r["_rubro"] = normalizar(r["rubro_cnv"])
        reglas.append(r)
    return sorted(reglas, key=lambda r: r["orden"])

def moneda_de_texto(texto):
    """'Dolar Estadounidense Billete' -> 'USD billete'; 'Peso Argentina' -> 'ARS'."""
    t = normalizar(texto)
    if "dolar" in t:
        return "USD billete" if "billete" in t else "USD"
    if "peso" in t:
        return "ARS"
    return None

def _moneda(regla, rubro, moneda_fondo):
    m = regla["moneda"]
    if m == "auto":
        return moneda_de_texto(rubro)
    if m == "fondo":
        return moneda_de_texto(moneda_fondo)
    return m or None

def _clasificar_reglas(detalle, rubro, moneda_fondo, reglas):
    """Devuelve un dict con categoria_resumen, categoria_detallada, moneda, indexacion, regla."""
    d, rb = normalizar(detalle), normalizar(rubro)
    candidatas = sorted(reglas, key=lambda r: (r["_rubro"] != "cafci", r["orden"])) if rb == "cafci" else reglas
    for r in candidatas:
        if r["_rubro"] and r["_rubro"] not in rb:
            continue
        if r["_re"] and not r["_re"].search(d):
            continue
        moneda = _moneda(r, rubro, moneda_fondo)
        if "{moneda}" in r["categoria_detallada"] or "{mon}" in r["categoria_detallada"]:
            if not moneda:
                if rb == 'cafci':
                    continue  # CAFCI sin moneda: probar reglas explícitas sin inventarla.
                break  # no se puede saber la moneda: queda sin clasificar
        pais = re.sub(r"(?i)^participaciones accionarias\s*", "", rubro).strip()
        detallada = r["categoria_detallada"].format(
            moneda=moneda or "", mon=(moneda or "").split(" ")[0], pais=pais).strip()
        return {"categoria_resumen": r["categoria_resumen"], "categoria_detallada": detallada,
                "moneda": moneda, "indexacion": r["indexacion"] or None, "regla": r["orden"]}
    return {"categoria_resumen": SIN_CLASIFICAR, "categoria_detallada": SIN_CLASIFICAR,
            "moneda": None, "indexacion": None, "regla": None}

def clasificar_linea(detalle, rubro, moneda_fondo, reglas, contadores=None):
    """Clasificación común en ingesta; las reglas de organización se aplican después."""
    return completar(_clasificar_reglas(detalle, rubro, moneda_fondo, reglas),
                     detalle, rubro, contadores)

def clasificar_cartera(cartera, reglas, tablas=True):
    """Clasifica las líneas que son tenencias: instrumentos y rubros sin detalle debajo.

    Devuelve una lista de filas (las de la cartera + las columnas de clasificación).
    """
    filas = cartera["filas"]
    con_hijos = {f["rubro"] for f in filas if f["nivel"] > 0}
    salida = []
    contadores = {}
    for orden, f in enumerate(filas, start=1):
        if f["nivel"] == 0 and f["detalle"] in con_hijos:
            continue  # es un título de rubro con su detalle debajo
        pct = f["pct_pn"] if f["nivel"] > 0 else f["pct_rubro"]
        c = (clasificar_linea(f["detalle"], f["rubro"], cartera["moneda"], reglas, contadores) if tablas
             else _clasificar_reglas(f["detalle"], f["rubro"], cartera["moneda"], reglas))
        salida.append({**f, "orden": orden, "pct": pct, **c})
    if contadores.get('conflictos_moneda'):
        print(f"CLASIFICACION conflictos_moneda={contadores['conflictos_moneda']}")
    return salida

# Fuente: robot/calcular_rendimientos.py
def _sin_patrimonio(f):
    return f.get("patrimonio") is not None and f["patrimonio"] == 0

def _hace_un_anio(f):
    try:
        return f.replace(year=f.year - 1)
    except ValueError:  # 29 de febrero
        return f.replace(year=f.year - 1, day=28)

def _rend(cp, base):
    return ((cp / base - 1) * 100).quantize(Decimal("0.000001"))

def _base(fechas, valores, objetivo):
    """Última cuotaparte con fecha <= objetivo, o None si no existe o está demasiado lejos."""
    i = bisect_right(fechas, objetivo)
    if i == 0 or (objetivo - fechas[i - 1]).days > MAX_HUECO_DIAS or valores[i - 1] <= 0:
        return None
    return valores[i - 1]

def calcular_clase(filas):
    """`filas`: valores de UNA clase ordenados por fecha. Devuelve las filas de `rendimientos`."""
    serie = [f for f in filas if f["cuotaparte"] is not None and not f["marca_estado"] and not _sin_patrimonio(f)]
    fechas, valores = [f["fecha"] for f in serie], [f["cuotaparte"] for f in serie]
    salida = []
    for f in filas:
        r = {"codigo_cafci": f["codigo_cafci"], "fecha": f["fecha"], "diario": None, "dias_7": None,
             "dias_30": None, "mes": None, "anio": None, "meses_12": None,
             "diferencia_vs_cafci": None, "revisar": False, "nota": ""}
        salida.append(r)
        if f["estado_fondo"] == "en liquidación":
            r["nota"] = "fondo en liquidación: sin rendimiento"
            continue
        if f["marca_estado"]:
            r["nota"] = f"clase con marca {f['marca_estado']}: sin rendimiento"
            continue
        if f["cuotaparte"] is None:
            r["nota"] = "sin cuotaparte"
            continue
        if _sin_patrimonio(f):
            r["nota"] = "clase sin patrimonio: sin rendimiento"
            continue
        t, cp = f["fecha"], f["cuotaparte"]
        # Diario: la última cuotaparte guardada anterior a la fecha.
        base_d = _base(fechas, valores, t - timedelta(days=1))
        if base_d is not None:
            r["diario"] = _rend(cp, base_d)
            if f["variacion_diaria_cafci"] is not None:
                r["diferencia_vs_cafci"] = (r["diario"] - f["variacion_diaria_cafci"]).quantize(Decimal("0.000001"))
                if abs(r["diferencia_vs_cafci"]) > TOLERANCIA_CAFCI:
                    r["revisar"] = True
        objetivos = {
            "dias_7": t - timedelta(days=7),
            "dias_30": t - timedelta(days=30),
            "mes": t.replace(day=1) - timedelta(days=1),
            "anio": date(t.year - 1, 12, 31),
            "meses_12": _hace_un_anio(t),
        }
        difs_cafci = []
        for campo, obj in objetivos.items():
            b = _base(fechas, valores, obj)
            if b is not None:
                r[campo] = _rend(cp, b)
        for campo, clave in (("mes", "rend_mes_cafci"), ("anio", "rend_anio_cafci"), ("meses_12", "rend_12m_cafci")):
            if r[campo] is not None and f.get(clave) is not None:
                dif = (r[campo] - f[clave]).quantize(Decimal("0.000001"))
                if abs(dif) > TOLERANCIA_CAFCI:
                    r["revisar"] = True
                    difs_cafci.append(f"{campo} difiere de la CAFCI en {dif} puntos")
        vacios = [c for c in (["diario"] if r["diario"] is None else []) + CAMPOS if r[c] is None]
        notas = []
        if vacios:
            notas.append("historia insuficiente: " + ", ".join(vacios))
        if r["diferencia_vs_cafci"] is not None and abs(r["diferencia_vs_cafci"]) > TOLERANCIA_CAFCI:
            notas.append(f"diario calculado difiere de la CAFCI en {r['diferencia_vs_cafci']} puntos")
        notas += difs_cafci
        r["nota"] = "; ".join(notas)
    return salida

# Fuente: robot/rubro.py
def tres_mayores(lineas):
    """`lineas`: iterable de (categoria_resumen, categoria_detallada, pct_pn).
    Devuelve [(categoria_detallada, categoria_resumen, pct)] con las 3 mayores, de mayor a menor."""
    por_detallada, resumen_de = {}, {}
    for resumen, detallada, pct in lineas:
        if resumen in NO_CUENTAN or pct is None or pct <= 0:
            continue
        por_detallada[detallada] = por_detallada.get(detallada, 0) + pct
        resumen_de[detallada] = resumen
    # Si dos empatan, manda el orden alfabético (así el resultado siempre es el mismo).
    orden = sorted(por_detallada.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return [(det, resumen_de[det], round(pct, 4)) for det, pct in orden]

def elegir_rubro(mayores):
    """El resumen que se repite al menos 2 veces entre las 3 mayores; si no hay repetido, el de la primera."""
    if not mayores:
        return None
    cuenta = Counter(resumen for _, resumen, _ in mayores)
    resumen, veces = cuenta.most_common(1)[0]
    return resumen if veces >= 2 else mayores[0][1]

def rubro_de_cartera(lineas):
    """Devuelve (rubro, 3 mayores tenencias)."""
    mayores = tres_mayores(lineas)
    return elegir_rubro(mayores), mayores

