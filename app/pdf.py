"""Geração de PDF (reportlab) a partir da mesma estrutura usada nos relatórios.

dados = {"cards": [(rótulo, valor, formato)], "tabelas": [{"titulo", "colunas": [(chave, rótulo, formato)],
         "linhas": [dict]}], "nota": str}
formatos: texto, horas (minutos), moeda, num, pct, data
"""
from __future__ import annotations

import datetime as dt
import io

from flask import Response

from . import util

AZUL = "#1F4E79"


def _fmt(v, fmt):
    if v is None or v == "":
        return "—"
    if fmt == "horas":
        return util.horas(v)
    if fmt == "horas_dec":
        return util.horas_dec(v)
    if fmt == "moeda":
        return util.moeda(v)
    if fmt == "pct":
        return f"{float(v):.1f}%".replace(".", ",")
    if fmt == "data":
        return util.data(v)
    if fmt == "data_hora":
        return util.data_hora(v)
    if fmt == "num" and not isinstance(v, int):
        return f"{float(v):.2f}".replace(".", ",")
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    if isinstance(v, (dt.datetime,)):
        return util.data_hora(v)
    if isinstance(v, dt.date):
        return util.data(v)
    return str(v)


def _logo():
    """Logo da empresa (configurações) ou o padrão; None se não der para ler."""
    try:
        from .db import db
        row = db().one("SELECT arquivo, tipo_mime FROM configuracoes WHERE chave = 'logo'")
    except Exception:  # noqa: BLE001 - PDF sai sem logo
        row = None
    if row and row["tipo_mime"] in ("image/png", "image/jpeg"):
        return io.BytesIO(bytes(row["arquivo"]))
    import pathlib
    p = pathlib.Path(__file__).parent / "static" / "logo-padrao.png"
    return io.BytesIO(p.read_bytes()) if p.exists() else None


def gerar(titulo: str, subtitulo: str, dados: dict, paisagem: bool = True) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    pagina = landscape(A4) if paisagem else A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=pagina, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=14 * mm, title=titulo, author="Porto Soluções Corporativas")
    est = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=est["Heading1"], fontSize=15, spaceAfter=2, textColor=colors.HexColor(AZUL))
    h2 = ParagraphStyle("h2", parent=est["Heading2"], fontSize=11, leading=14, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor(AZUL))
    pq = ParagraphStyle("pq", parent=est["Normal"], fontSize=8, leading=10)
    pqd = ParagraphStyle("pqd", parent=pq, alignment=TA_RIGHT)
    cab = ParagraphStyle("cab", parent=pq, textColor=colors.white, fontName="Helvetica-Bold")
    cabd = ParagraphStyle("cabd", parent=cab, alignment=TA_RIGHT)
    sub = ParagraphStyle("sub", parent=est["Normal"], fontSize=9, textColor=colors.HexColor("#555555"))

    story = []
    logo = _logo()
    cabecalho = [Paragraph(util_escape(titulo), h1), Paragraph(util_escape(subtitulo), sub)]
    if logo:
        try:
            img = Image(logo, width=38 * mm, height=14 * mm, kind="proportional")
            t = Table([[cabecalho, img]], colWidths=[doc.width - 42 * mm, 42 * mm])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
            story.append(t)
        except Exception:  # noqa: BLE001 - imagem inválida: segue sem logo
            story += cabecalho
    else:
        story += cabecalho
    story.append(Spacer(1, 6))

    cards = dados.get("cards") or []
    if cards:
        cel = [[Paragraph(f"<font size=7 color='#555555'>{util_escape(r)}</font><br/><b>{util_escape(_fmt(v, f))}</b>",
                          pq) for r, v, f in cards]]
        n = len(cards)
        t = Table(cel, colWidths=[doc.width / n] * n)
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d3df")),
                               ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d3df")),
                               ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fb")),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [t, Spacer(1, 4)]

    direita = {"horas", "horas_dec", "moeda", "num", "pct"}
    for tab in dados.get("tabelas") or []:
        cols = tab["colunas"]
        bloco = [Paragraph(util_escape(tab["titulo"]), h2)]
        if not tab["linhas"]:
            bloco.append(Paragraph("<i>Sem dados no período.</i>", pq))
            story.append(KeepTogether(bloco))
            continue
        dados_t = [[Paragraph(util_escape(c[1]), cabd if c[2] in direita else cab) for c in cols]]
        for l in tab["linhas"]:
            dados_t.append([Paragraph(util_escape(_fmt(l.get(k), f)), pqd if f in direita else pq)
                            for k, _r, f in cols])
        if tab.get("total"):
            dados_t.append([Paragraph(f"<b>{util_escape(_fmt(tab['total'].get(k), f)) if k in tab['total'] else ''}</b>",
                                      pqd if f in direita else pq) for k, _r, f in cols])
        larg = tab.get("larguras")
        if larg:
            tot = sum(larg)
            larg = [doc.width * x / tot for x in larg]
        t = Table(dados_t, colWidths=larg, repeatRows=1)
        estilo = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL)),
                  ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7fb")]),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d8dee6")),
                  ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if tab.get("total"):
            estilo.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e6edf5")))
        t.setStyle(TableStyle(estilo))
        bloco.append(t)
        if tab.get("obs"):
            bloco += [Spacer(1, 3), Paragraph(util_escape(tab["obs"]), sub)]
        story += bloco
    if dados.get("nota"):
        story += [Spacer(1, 8), Paragraph(util_escape(dados["nota"]), sub)]

    gerado = dt.datetime.now(util.TZ).strftime("%d/%m/%Y %H:%M")

    def rodape(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(d.leftMargin, 8 * mm, f"Porto Soluções Corporativas · SisPorto · gerado em {gerado}")
        canvas.drawRightString(pagina[0] - d.rightMargin, 8 * mm, f"Página {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()


def util_escape(t) -> str:
    return (str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"))


def resposta(nome_arquivo: str, conteudo: bytes) -> Response:
    from urllib.parse import quote
    return Response(conteudo, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(nome_arquivo)}"})
