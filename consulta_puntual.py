"""Consulta pública en memoria; solo GET, sin base, archivos ni pedidos.

El diccionario cache pertenece a la sesión. Nunca almacena el token.
"""
import hashlib
import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pandas as pd
import requests

import fuentes_consulta as robot

LEYENDA = "Consulta en el momento: este fondo no se sigue y no se guarda."
URL_CNV = "https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion"
CABECERAS_CNV = {"User-Agent": "Mozilla/5.0 (compatible; robot-fci)"}
BASE_GITHUB = "https://api.github.com/repos/dhvairo/robot-fci"
SERIE_COLUMNAS = ["codigo_cafci", "fecha", "cuotaparte", "patrimonio"]


class ErrorConsulta(Exception):
    """Aviso seguro: no revela credenciales ni respuestas remotas."""


def token_configurado(secrets):
    for nombre in ("GH_TOKEN", "GITHUB_TOKEN"):
        try:
            valor = secrets.get(nombre)
        except Exception:
            valor = None
        if valor:
            return valor
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _get(url, headers, binario=False):
    try:
        r = requests.get(url, headers=headers, timeout=(5, 15))
        if r.status_code != 200:
            raise ErrorConsulta(f"La fuente respondió HTTP {r.status_code}.")
        return r.content if binario else r.json()
    except (requests.RequestException, ValueError):
        raise ErrorConsulta("La fuente no respondió o devolvió datos ilegibles.") from None


def historia_github(fondo, token):
    if not token:
        raise ErrorConsulta("Falta GH_TOKEN en los Secrets de Streamlit (GITHUB_TOKEN también sirve), "
                            "con lectura de Contents del repositorio privado dhvairo/robot-fci. "
                            "Claude debe cargarlo por Chrome.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    releases = []
    pagina = 1
    while True:
        lote = _get(f"{BASE_GITHUB}/releases?per_page=100&page={pagina}", headers)
        releases.extend(r for r in lote if re.fullmatch(r"historia-\d{4}-\d{2}", r["tag_name"])
                        and r["tag_name"][9:] >= "2024-01")
        if len(lote) < 100:
            break
        pagina += 1
    if not releases:
        raise ErrorConsulta("No hay historia archivada desde enero de 2024.")
    headers = {**headers, "Accept": "application/octet-stream"}

    def leer_mes(release):
        import json
        assets = {a["name"]: a for a in release["assets"]}
        if "manifiesto.json" not in assets:
            raise ErrorConsulta("La historia no tiene manifiesto de integridad.")
        manifiesto = json.loads(_get(assets["manifiesto.json"]["url"], headers, True))
        ruta = "historia/" + release["tag_name"][9:] + ".csv.gz"
        fila = next((f for f in reversed(manifiesto) if f["ruta_supabase"] == ruta), None)
        if not fila or fila["nombre_github"] not in assets:
            raise ErrorConsulta("Falta un adjunto de historia.")
        datos = _get(assets[fila["nombre_github"]]["url"], headers, True)
        if len(datos) != fila["size"] or hashlib.sha256(datos).hexdigest() != fila["sha256"]:
            raise ErrorConsulta("El archivo de historia no coincide con su SHA-256.")
        columnas = ["codigo_cnv", "codigo_cafci", "fecha", "cuotaparte", "patrimonio", "marca_estado",
                    "variacion_diaria_pct", "rend_mes_pct", "rend_anio_pct", "rend_12m_pct"]
        tipos = {col: str for col in columnas if col not in ("codigo_cnv", "codigo_cafci")}
        df = pd.read_csv(io.BytesIO(datos), compression="gzip", usecols=columnas,
                         dtype=tipos, keep_default_na=False)
        df = df[df["codigo_cnv"] == int(fondo["codigo_cnv"])]
        # Última versión recibida, incluso cuando la última trae una marca de estado.
        return df.drop_duplicates(["codigo_cafci", "fecha"], keep="last")

    with ThreadPoolExecutor(max_workers=8) as pool:
        partes = list(pool.map(leer_mes, sorted(releases, key=lambda r: r["tag_name"])))
    df = pd.concat(partes, ignore_index=True)
    if df.empty:
        raise ErrorConsulta("No hay historia archivada para este fondo desde enero de 2024.")
    df["codigo_cafci"] = df["codigo_cafci"].astype(int)
    df["fecha"] = df["fecha"].map(date.fromisoformat)
    campos = {"variacion_diaria_pct": "variacion_diaria_cafci", "rend_mes_pct": "rend_mes_cafci",
              "rend_anio_pct": "rend_anio_cafci", "rend_12m_pct": "rend_12m_cafci"}
    df = df.rename(columns=campos)
    for col in ["cuotaparte", "patrimonio", *campos.values()]:
        df[col] = df[col].map(lambda v: Decimal(v) if v else None)
    df["estado_fondo"] = fondo.get("estado", "")
    return df.sort_values(["codigo_cafci", "fecha"])


def cartera_cnv(fondo, cache):
    ident = fondo.get("id_ficha_web")
    if ident is None or pd.isna(ident):
        if "lista_cnv" not in cache:
            html = _get(URL_CNV, CABECERAS_CNV, True).decode("utf-8", errors="replace")
            cache["lista_cnv"] = robot.leer_lista(html)
        ident, motivo = robot.buscar_id(fondo["nombre"], cache["lista_cnv"])
        if ident is None:
            raise ErrorConsulta("Cartera no disponible: " + motivo + ".")
    html = _get(f"{URL_CNV}/DetallesFCI/{int(ident)}", CABECERAS_CNV, True).decode("utf-8", errors="replace")
    if robot.normalizar_nombre(robot.leer_cartera(html)["fondo"]) != robot.normalizar_nombre(fondo["nombre"]):
        raise ErrorConsulta("Cartera no disponible: la ficha CNV corresponde a otro fondo.")
    if fondo.get("gerente") and not robot.misma_gerente(fondo["gerente"], robot.gerente_de_ficha(html)):
        raise ErrorConsulta("Cartera no disponible: no coincide la sociedad gerente.")
    c = robot.leer_cartera(html)
    filas = robot.clasificar_cartera(c, robot.cargar_reglas())
    cart = pd.DataFrame(filas).rename(columns={"detalle": "instrumento", "rubro": "rubro_cnv"})
    cart["pct_pn"] = cart.pop("pct")
    cart["sin_clasificar"] = cart["regla"].isna()
    cart = cart[["orden", "rubro_cnv", "instrumento", "pct_pn", "categoria_resumen",
                 "categoria_detallada", "moneda", "indexacion", "sin_clasificar"]]
    enc = pd.DataFrame([{"fecha_cartera": c["fecha_cartera"], "moneda": c["moneda"],
                         "patrimonio": c["patrimonio"], "instrumentos": len(cart),
                         "sin_clasificar": int(cart.sin_clasificar.sum()),
                         "pct_sin_clasificar": cart.loc[cart.sin_clasificar, "pct_pn"].sum()}])
    rubro, mayores = robot.rubro_de_cartera(cart[["categoria_resumen", "categoria_detallada", "pct_pn"]]
                                         .itertuples(index=False, name=None))
    return enc, cart, {"categoria": mayores[0][0] if mayores else "Sin cartera", "rubro": rubro,
                       "fecha_cartera": c["fecha_cartera"], "clasificacion_cnv": robot.leer_clasificacion(html)}


def efectiva(resultado, fondo, componer=None):
    """Compone una copia; la caché de sesión conserva exclusivamente la fuente.

    Se ejecuta en el hilo de la pantalla con Auth de servidor, nunca en el pool.
    No descarga ni persiste; las siguientes sesiones revalidan su propio contexto.
    """
    from copy import deepcopy
    import calculos
    if componer is None:
        import datos
        componer = datos.componer_carteras_organizacion
    salida = deepcopy(resultado)
    cart, enc = salida['cartera'], salida['encabezado']
    if cart.empty or enc.empty:
        return salida
    fuente = cart.copy(deep=True)
    fuente['codigo_cnv'] = int(fondo['codigo_cnv'])
    fuente['fecha_cartera'] = enc.iloc[0]['fecha_cartera']
    fuente['rubro_cnv_original'] = fuente.rubro_cnv
    fuente['instrumento_original'] = fuente.instrumento
    fuente['moneda_del_fondo_original'] = enc.iloc[0]['moneda']
    vista = componer(fuente.astype(object).where(pd.notna(fuente), None).to_dict('records'))
    salida['clasificacion'] = vista
    if vista['estado'] == 'deshabilitado':
        salida['avisos'].append(vista['mensaje'])
        return salida
    salida['cartera'] = calculos.filas_efectivas(vista['filas'])[cart.columns]
    salida['encabezado'] = calculos.encabezado_efectivo(enc, vista, int(fondo['codigo_cnv']))
    resumen = vista['resumenes'][0]
    salida['cabecera'].update(rubro=resumen['rubro'], categoria=(
        resumen['tenencias'][0][0] if resumen['tenencias'] else 'Sin cartera'))
    if resumen['bloqueados']:
        motivos = sorted({f['bloqueo'] for f in vista['filas'] if f['bloqueo']})
        salida['avisos'].append('Se conserva la fuente por identidad: ' + ', '.join(motivos) + '.')
    return salida


def cargar(fondo, cache, token=None, leer_historia=historia_github, leer_cartera=cartera_cnv):
    """Reutilizable por Ficha/Comparador; cache es un dict de st.session_state.

    Cada fuente falla por separado. Los avisos y resultados se conservan durante la sesión.
    """
    codigo = int(fondo["codigo_cnv"])
    consultas = cache.setdefault("fondos", {})
    if codigo in consultas:
        return consultas[codigo]
    inicio = time.perf_counter()
    resultado = {"serie": pd.DataFrame(columns=SERIE_COLUMNAS), "rendimientos": pd.DataFrame(),
                 "encabezado": pd.DataFrame(), "cartera": pd.DataFrame(), "cabecera": {}, "avisos": []}
    pool = ThreadPoolExecutor(max_workers=2)
    historia = pool.submit(leer_historia, fondo, token)
    cartera = pool.submit(leer_cartera, fondo, cache)
    try:
        df = historia.result()
        rend = []
        for _, grupo in df.groupby("codigo_cafci"):
            filas = grupo.sort_values("fecha").to_dict("records")
            ultimo = robot.calcular_clase(filas)[-1]
            rend.append({**ultimo, "cuotaparte": filas[-1]["cuotaparte"], "patrimonio": filas[-1]["patrimonio"]})
        resultado["rendimientos"] = pd.DataFrame(rend)
        resultado["serie"] = df[SERIE_COLUMNAS].copy()
        resultado["serie"].loc[df["marca_estado"].ne(""), ["cuotaparte", "patrimonio"]] = None
    except (ErrorConsulta, ValueError, KeyError, OSError):
        # Un mensaje explícito para configuración incompleta, sin incluir el token.
        resultado["avisos"].append("Historia no disponible. " + (
            "Falta GH_TOKEN (o GITHUB_TOKEN) en Streamlit; Claude debe cargarlo con lectura de Contents "
            "de dhvairo/robot-fci." if not token else "No se pudo leer o verificar la historia de GitHub."))
    try:
        enc, cart, cab = cartera.result()
        resultado.update(encabezado=enc, cartera=cart, cabecera=cab)
    except (ErrorConsulta, robot.FichaInvalida, ValueError, KeyError, OSError):
        resultado["avisos"].append("Cartera no disponible: la CNV no respondió, no se encontró una ficha "
                                    "única o no se pudo validar. La historia disponible se muestra igual.")
    finally:
        pool.shutdown(wait=True)
    resultado["segundos"] = round(time.perf_counter() - inicio, 2)
    consultas[codigo] = resultado
    return resultado
