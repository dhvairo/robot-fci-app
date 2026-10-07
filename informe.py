"""Informe del Resumen masivo de fondos: los datos de cada página y el PDF (pedido 3.1 de octubre).

`secciones` arma, a partir de la tabla del Resumen masivo, lo que muestran la pantalla y el PDF (una sección por moneda:
título, subtítulo, torta con una porción igual por fondo y tabla Fondo | Principales rubros | Idea y temática).
`pdf` dibuja esas secciones con ReportLab (todo Python: funciona igual en la PC y en Streamlit Cloud, sin programas externos).
"""
from io import BytesIO
from math import cos, radians, sin
from xml.sax.saxutils import escape

import formato

TITULO_BASE = "Fondos Comunes de Inversión en"
FUENTE = "Fuente: Comisión Nacional de Valores (CNV), Composición Semanal de Cartera."
ACLARACION = "Porcentajes sobre el patrimonio neto de cada fondo. Sub-soberano = provincias; CER = ajuste por inflación."
DESCARGO = ("Información de carácter exclusivamente informativo, elaborada a partir de datos públicos de la CNV. No "
            "constituye recomendación, asesoramiento ni oferta de inversión. Las carteras de los fondos cambian en forma "
            "permanente y los rendimientos pasados no garantizan rendimientos futuros. Antes de invertir, consulte el "
            "reglamento de gestión de cada fondo.")
# Colores de las porciones (los del informe modelo; si hay más fondos, se repiten).
PALETA = ("#1F3864", "#2E75B6", "#5B9BD5", "#9DC3E6", "#C9A227", "#808080", "#6AA84F", "#E06666", "#8E7CC3",
          "#F6B26B", "#76A5AF", "#B4A7D6")
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
                    "descargo": DESCARGO})
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
                        textAnchor="middle", fillColor=colors.HexColor("#1F3864")))
    paso = 360 / n
    rotulos = {"izq": [], "der": []}
    for i, f in enumerate(fondos):
        a0, a1 = 90 - (i + 1) * paso, 90 - i * paso            # sentido de las agujas, arrancando arriba (como el modelo)
        d.add(shapes.Wedge(cx, cy, radio, a0, a1, fillColor=colors.HexColor(PALETA[i % len(PALETA)]),
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

    azul = colors.HexColor("#1F3864")
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
        p = Paragraph(escape(DESCARGO), s_descargo)
        _, alto_p = p.wrap(ancho, 3 * cm)
        p.drawOn(canvas, margen, 1.0 * cm)
        canvas.restoreState()

    historia = []
    for i, sec in enumerate(secs):
        if i:
            historia.append(PageBreak())
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
                            author="Robot FCI")
    doc.build(historia, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
