"""Informe del Resumen masivo de fondos: los datos de cada página y el PDF (pedido 3.1 de octubre).

`secciones` arma, a partir de la tabla del Resumen masivo, lo que muestran la pantalla y el PDF (una sección por moneda:
título, subtítulo, torta con una porción igual por fondo y tabla Fondo | Principales rubros | Idea y temática).
`pdf` dibuja esas secciones con ReportLab (todo Python: funciona igual en la PC y en Streamlit Cloud, sin programas externos).
"""
from io import BytesIO
from math import cos, radians, sin
from xml.sax.saxutils import escape

import formato
import marca

TITULO_BASE = "Fondos Comunes de Inversión en"
FUENTE = "Fuente: Comisión Nacional de Valores (CNV), Composición Semanal de Cartera."
ACLARACION = "Porcentajes sobre el patrimonio neto de cada fondo. Sub-soberano = provincias; CER = ajuste por inflación."
# Alias históricos para consumidores existentes; pantalla y PDF usan la marca actual.
DESCARGO = marca.DESCARGO
PALETA = (marca.PROPIA["color_principal"], marca.PROPIA["color_secundario"], *marca.PALETA_BASE)
MONEDAS = (("pesos", "pesos", ("ARS",)), ("dolares", "dólares", ("USD", "USB")))     # (clave, texto, códigos)


def _pct_corto(x):
    """78,51 -> '78,5%' ; 30,0 -> '30%' ; 0,35 -> '0,4%'."""
    return formato.numero_corto(round(float(x), 1), 0, 1) + "%"


def rubros_principales(fila):
    """Los 2 principales rubros del fondo: 'Soberano ARS CER 78,5%' (solo los que existen)."""
    out = []
    for nombre, pct in ((fila["1° tenencia (rubro / moneda / indexación)"], fila["% 1°"]),
                        (fila["2° tenencia"], fila["% 2°"])):
        if not formato._es_nulo(pct) and nombre != formato.VACIO:
            out.append(f"{nombre} {_pct_corto(pct)}")
    return out


def secciones(tabla):
    """Una sección por moneda con fondos (pesos primero, después dólares). `tabla` es la de `calculos.resumen_masivo`."""
    out = []
    for clave, texto, codigos in MONEDAS:
        t = tabla[tabla["_moneda"].isin(codigos)]
        if t.empty:
            continue
        fechas = sorted({f for f in t["Cartera al"] if not formato._es_nulo(f)})
        distintas = len(fechas) > 1
        if len(fechas) == 1:
            sub = f"Composición de cartera al {formato.fecha(fechas[0])} · {FUENTE}"
        elif distintas:
            sub = f"Composición de cartera según la fecha de cada fondo (se indica en la tabla) · {FUENTE}"
        else:
            sub = f"Composición de cartera (sin fecha disponible) · {FUENTE}"
        fondos = []
        for _, f in t.iterrows():
            rubros = rubros_principales(f)
            fondos.append({
                "nombre": f["Fondo"], "rubros": rubros, "idea": f["_idea"], "idea_pdf": f["_idea_pdf"],
                "fecha": formato.fecha(f["Cartera al"]) if distintas else None,
                "etiqueta": "\n".join([f["Fondo"]] + [f"{i}) {r}" for i, r in enumerate(rubros, start=1)])})
        out.append({"clave": clave, "titulo": f"{TITULO_BASE} {texto}", "subtitulo": sub, "aclaracion": ACLARACION,
                    "titulo_torta": f"Fondos en {texto} — dos principales rubros por fondo", "fondos": fondos,
                    "descargo": marca.actual()["texto_legal"]})
    return out


def nombre_archivo(secs, tabla):
    """FCI_Composicion_Pesos_y_Dolares_18-09-2026.pdf (la fecha es la de la cartera más nueva)."""
    monedas = "_y_".join({"pesos": "Pesos", "dolares": "Dolares"}[s["clave"]] for s in secs)
    fechas = [f for f in tabla["Cartera al"] if not formato._es_nulo(f)]
    fecha = formato.fecha(max(fechas)).replace("/", "-") if fechas else "sin_fecha"
    return f"FCI_Composicion_{monedas}_{fecha}.pdf"


# ───────── PDF ─────────

def _dibujar_torta(sec, ancho, colors, shapes):
    """Torta con una porción igual por fondo y su rótulo (nombre y 2 principales rubros) afuera, con línea guía."""
    fondos = sec["fondos"]
    n = len(fondos)
    por_lado = -(-n // 2)
    alto_rotulo = 28
    alto = max(220, por_lado * (alto_rotulo + 6) + 30)
    d = shapes.Drawing(ancho, alto)
    cx, cy, radio = ancho / 2, alto / 2, 78
    d.add(shapes.String(ancho / 2, alto - 12, sec["titulo_torta"], fontName="Helvetica-Bold", fontSize=10.5,
                        textAnchor="middle", fillColor=colors.HexColor(marca.actual()["color_principal"])))
    paso = 360 / n
    rotulos = {"izq": [], "der": []}
    for i, f in enumerate(fondos):
        a0, a1 = 90 - (i + 1) * paso, 90 - i * paso            # sentido de las agujas, arrancando arriba (como el modelo)
        paleta = marca.paleta()
        d.add(shapes.Wedge(cx, cy, radio, a0, a1, fillColor=colors.HexColor(paleta[i % len(paleta)]),
                           strokeColor=colors.white, strokeWidth=1.2))
        medio = radians((a0 + a1) / 2)
        lado = "der" if cos(medio) >= 0 else "izq"
        rotulos[lado].append((cx + radio * cos(medio), cy + radio * sin(medio), sin(medio), f["etiqueta"]))
    for lado, lista in rotulos.items():
        lista.sort(key=lambda r: -r[2])                         # de arriba hacia abajo
        y_ant = None
        for px, py, _, texto in lista:
            y = py + 14
            if y_ant is not None and y > y_ant - (alto_rotulo + 6):
                y = y_ant - (alto_rotulo + 6)                   # que los rótulos no se pisen
            y = min(max(y, 24), alto - 30)
            y_ant = y
            x = ancho - 4 if lado == "der" else 4
            xr = (cx + radio + 38) if lado == "der" else (cx - radio - 38)
            d.add(shapes.Line(px, py, xr, y, strokeColor=colors.HexColor("#999999"), strokeWidth=0.6))
            d.add(shapes.Line(xr, y, xr + (6 if lado == "der" else -6), y, strokeColor=colors.HexColor("#999999"),
                              strokeWidth=0.6))
            for k, linea in enumerate(texto.split("\n")):
                d.add(shapes.String(xr + (9 if lado == "der" else -9), y - 3 - k * 9.5, linea,
                                    fontName="Helvetica-Bold" if k == 0 else "Helvetica", fontSize=7.5,
                                    textAnchor="start" if lado == "der" else "end"))
    return d


def pdf(secs, titulo_documento=None):
    """Devuelve los bytes del PDF: una página por moneda (más si hay muchos fondos), con título, subtítulo, torta, tabla y descargo."""
    from reportlab.graphics import shapes
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    config = marca.actual()
    azul = colors.HexColor(config["color_principal"])
    s_titulo = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=azul, spaceAfter=3)
    s_sub = ParagraphStyle("s", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    s_celda = ParagraphStyle("c", fontName="Helvetica", fontSize=8, leading=10, alignment=TA_LEFT)
    s_negrita = ParagraphStyle("n", parent=s_celda, fontName="Helvetica-Bold")
    s_cab = ParagraphStyle("h", parent=s_celda, fontName="Helvetica-Bold", textColor=colors.white)
    s_fecha = ParagraphStyle("f", parent=s_celda, fontSize=6.5, leading=8, textColor=colors.HexColor("#666666"))
    s_descargo = ParagraphStyle("d", fontName="Helvetica-Oblique", fontSize=6.5, leading=8.2,
                                textColor=colors.HexColor("#888888"))
    margen = 1.6 * cm
    ancho = A4[0] - 2 * margen

    def pie(canvas, doc):
        canvas.saveState()
        p = Paragraph(escape(config["texto_legal"]), s_descargo)
        _, alto_p = p.wrap(ancho, 3 * cm)
        p.drawOn(canvas, margen, 1.0 * cm)
        if config["nombre"] != marca.PROPIA["nombre"]:
            canvas.setFillColor(azul)
            canvas.setFont("Helvetica", 7)
            canvas.drawString(margen, 0.5 * cm, config["nombre"])
        canvas.restoreState()

    historia = []
    logo = marca.logo_bytes()
    for i, sec in enumerate(secs):
        if i:
            historia.append(PageBreak())
        if logo:
            from reportlab.platypus import Image
            imagen = Image(BytesIO(logo))
            factor = min(100 / imagen.imageWidth, 35 / imagen.imageHeight)
            imagen.drawWidth = imagen.imageWidth * factor
            imagen.drawHeight = imagen.imageHeight * factor
            imagen.hAlign = "LEFT"
            historia.extend([imagen, Spacer(1, 4)])
        if "contenido" in sec:
            historia += [Paragraph(escape(sec["titulo"]), s_titulo), Spacer(1, 8)]
            for bloque in sec["contenido"]:
                if isinstance(bloque, str):
                    historia += [Paragraph(escape(bloque), s_sub), Spacer(1, 6)]
                elif isinstance(bloque, tuple):
                    titulo, tabla = bloque
                    if tabla.empty:
                        continue
                    historia += [Paragraph(escape(titulo), s_negrita), Spacer(1, 5)]
                    filas = [[Paragraph(escape(str(c)), s_cab) for c in tabla.columns]]
                    filas += [[Paragraph(f'<font color="{v}">------</font>' if c == "Color" else escape(str(v)), s_celda)
                               for c, v in zip(tabla.columns, fila)] for fila in tabla.itertuples(index=False, name=None)]
                    # La primera columna contiene los nombres de clase/instrumento.
                    n = len(tabla.columns)
                    anchos = [ancho] if n == 1 else [ancho * .28] + [ancho * .72 / (n - 1)] * (n - 1)
                    t = Table(filas, colWidths=anchos, repeatRows=1, hAlign="LEFT")
                    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), azul),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#D0D7E2")),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF2F8")])]))
                    historia += [t, Spacer(1, 10)]
                else:
                    historia += [bloque, Spacer(1, 10)]
            continue
        historia += [Paragraph(escape(sec["titulo"]), s_titulo), Paragraph(escape(sec["subtitulo"]), s_sub),
                     Paragraph(escape(sec["aclaracion"]), s_sub), Spacer(1, 6),
                     _dibujar_torta(sec, ancho, colors, shapes), Spacer(1, 8)]
        filas = [[Paragraph("Fondo", s_cab), Paragraph("Principales rubros", s_cab), Paragraph("Idea y temática", s_cab)]]
        for f in sec["fondos"]:
            fondo = [Paragraph(escape(f["nombre"]), s_negrita)]
            if f["fecha"]:
                fondo.append(Paragraph(f"Cartera al {f['fecha']}", s_fecha))
            filas.append([fondo, Paragraph("<br/>".join(escape(r) for r in f["rubros"]) or formato.VACIO, s_celda),
                          Paragraph(escape(f["idea_pdf"]), s_celda)])
        t = Table(filas, colWidths=[ancho * 0.24, ancho * 0.27, ancho * 0.49], repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), azul), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D7E2")),
                  ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        for r in range(2, len(filas), 2):
            estilo.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#EEF2F8")))
        t.setStyle(TableStyle(estilo))
        historia.append(t)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margen, rightMargin=margen, topMargin=1.5 * cm,
                            bottomMargin=2.6 * cm, title=titulo_documento or " / ".join(s["titulo"] for s in secs),
                            author=config["nombre"])
    doc.build(historia, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()


def pdf_ficha(fondo, clases, serie, rubros, tenencias, notas=(), costos=None):
    """Ficha con las selecciones actuales; reutiliza estilo, marca y pie legal de pdf()."""
    import calculos
    import filtros
    import pandas as pd
    from reportlab.graphics import shapes
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.lib import colors

    contenido = [f"Código CNV {fondo['codigo_cnv']} · {formato.texto_o_guion(fondo.get('estado'))}"]
    contenido += [f"{etiqueta}: {valor}" for etiqueta, valor in calculos.cabecera_fondo(fondo)]
    contenido += ["Descripción breve: " + formato.texto_o_guion(fondo.get("descripcion_breve")),
                  "Idea y temática: " + formato.texto_o_guion(fondo.get("tematica"))]
    if not clases.empty:
        valores = [c for c in clases if not c.endswith("%")]
        rendimientos = ["Clase"] + [c for c in clases if c.endswith("%")]
        contenido += [("Clases", clases[valores]), ("Rendimientos", clases[rendimientos])]
    else:
        contenido.append("Sin clases seleccionadas o disponibles.")
    if costos is not None and not costos.empty:
        # Costos por clase en vertical para conservar legibilidad en A4.
        for _, fila in costos.iterrows():
            contenido.append((f"Costos y calificación: {fila.get('Clase', '')}", pd.DataFrame(
                [(c, v) for c, v in fila.items() if c != "Clase"], columns=["Dato", "Valor"])))
    contenido += list(notas)
    if not fondo["seguido"] and not fondo.get("consulta_puntual"):
        contenido.append(filtros.AVISO_NO_SEGUIDO)
    else:
        if not serie.empty and serie["fecha"].nunique() >= 2:
            # Dibujo vectorial: no necesita Kaleido ni un navegador en Streamlit.
            dibujo = shapes.Drawing(500, 245)
            dibujo.add(shapes.String(0, 231, "Evolución de la cuotaparte (base 100)", fontName="Helvetica-Bold", fontSize=10))
            bajo, alto = float(serie["base 100"].min()), float(serie["base 100"].max())
            if alto == bajo:
                alto = bajo + 1
            inicio, fin = serie.fecha.min(), serie.fecha.max()
            for k in range(5):
                y = 48 + k * 35
                dibujo.add(shapes.Line(48, y, 490, y, strokeColor=colors.HexColor("#D0D7E2")))
                dibujo.add(shapes.String(0, y - 3, formato.numero(bajo + (alto - bajo) * k / 4), fontSize=7))
            for i, (_, grupo) in enumerate(serie.groupby("codigo_cafci")):
                grupo = grupo.sort_values("fecha")
                puntos = []
                for _, r in grupo.iterrows():
                    puntos += [48 + (r.fecha - inicio).days / max(1, (fin - inicio).days) * 442,
                               48 + (float(r["base 100"]) - bajo) / (alto - bajo) * 140]
                color = colors.HexColor(marca.paleta()[i % len(marca.paleta())])
                dibujo.add(shapes.PolyLine(puntos, strokeColor=color, strokeWidth=1.2))
            dibujo.add(shapes.String(48, 30, formato.fecha(inicio), fontSize=8))
            dibujo.add(shapes.String(490, 30, formato.fecha(fin), textAnchor="end", fontSize=8))
            contenido.append(dibujo)
            contenido.append(("Clases del gráfico", pd.DataFrame([
                (g.iloc[0]["nombre"], marca.paleta()[i % len(marca.paleta())])
                for i, (_, g) in enumerate(serie.groupby("codigo_cafci"))], columns=["Clase", "Color"])))
        else:
            contenido.append("Historia insuficiente para el gráfico de evolución.")
        if not rubros.empty:
            dibujo = shapes.Drawing(500, 215)
            dibujo.add(shapes.String(0, 200, "Cartera por rubro (% del patrimonio)", fontName="Helvetica-Bold", fontSize=10))
            torta = Pie()
            torta.x, torta.y, torta.width, torta.height = 170, 5, 180, 180
            torta.data = rubros.pct_pn.astype(float).tolist()
            torta.labels = None
            for i in range(len(rubros)):
                torta.slices[i].fillColor = colors.HexColor(marca.paleta()[i % len(marca.paleta())])
            dibujo.add(torta)
            leyenda = formato.tabla(rubros.rename(columns={
                "categoria_resumen": "Rubro", "pct_pn": "% del PN"}), numeros={"% del PN": 2})
            leyenda["Color"] = [marca.paleta()[i % len(marca.paleta())] for i in range(len(leyenda))]
            contenido += [dibujo, ("Rubros", leyenda),
                ("10 mayores tenencias (selección actual)", tenencias)]
        else:
            contenido.append("Todavía no hay cartera cargada para este fondo.")
    return pdf([{"titulo": fondo["nombre"], "contenido": contenido}], titulo_documento=f"Ficha: {fondo['nombre']}")
