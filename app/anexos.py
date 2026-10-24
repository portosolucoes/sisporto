"""Anexos de chamados: apenas arquivos compactados, até 20 MB, guardados no banco."""
from __future__ import annotations

import re

from flask import Blueprint, Response, abort, g, request

from .acessos import pode
from .auth import login_obrigatorio
from .db import db

bp = Blueprint("anexos", __name__)

LIMITE_BYTES = 20 * 1024 * 1024
EXTENSOES = (".zip", ".rar", ".7z", ".gz", ".tgz", ".tar.gz", ".bz2", ".xz")
ASSINATURAS = {  # início do arquivo -> tipo
    b"PK\x03\x04": "application/zip", b"PK\x05\x06": "application/zip",
    b"Rar!\x1a\x07": "application/vnd.rar", b"7z\xbc\xaf\x27\x1c": "application/x-7z-compressed",
    b"\x1f\x8b": "application/gzip", b"BZh": "application/x-bzip2", b"\xfd7zXZ\x00": "application/x-xz",
}
MSG_GRANDE = ("O arquivo tem mais de 20 MB e não foi enviado. Coloque-o em uma pasta do Google Drive "
              "(ou similar) e cole o link na mensagem.")
MSG_TIPO = "Só são aceitos arquivos compactados (.zip, .rar, .7z, .gz). Compacte o arquivo e envie de novo."


def validar(arquivo):
    """Devolve (nome, bytes, mime) ou None se não há arquivo. Lança ValueError com a mensagem ao usuário."""
    if not arquivo or not arquivo.filename:
        return None
    nome = re.sub(r"[^\w.\- ()]", "_", arquivo.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])[:150]
    dados = arquivo.read(LIMITE_BYTES + 1)
    if len(dados) > LIMITE_BYTES:
        raise ValueError(MSG_GRANDE)
    if not dados:
        raise ValueError("O arquivo está vazio.")
    if not nome.lower().endswith(EXTENSOES):
        raise ValueError(MSG_TIPO)
    mime = next((m for sig, m in ASSINATURAS.items() if dados.startswith(sig)), None)
    if not mime:
        raise ValueError(MSG_TIPO + " (o conteúdo não parece ser um arquivo compactado)")
    return nome, dados, mime


def salvar(chamado_id, interacao_id, anexo, usuario_id):
    nome, dados, mime = anexo
    db().run("""INSERT INTO chamado_anexos (chamado_id, interacao_id, nome_arquivo, tamanho_bytes, tipo_mime,
                                           arquivo, enviado_por)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", [chamado_id, interacao_id, nome, len(dados), mime, dados, usuario_id])


def listar(chamado_id, incluir_internos=True):
    filtro = "" if incluir_internos else "AND (i.id IS NULL OR i.tipo = 'publica')"
    return db().all(f"""SELECT a.id, a.nome_arquivo, a.tamanho_bytes, a.criado_em, a.interacao_id, a.drive_url,
                               coalesce(u.nome, '—') AS autor, i.tipo
                          FROM chamado_anexos a
                          LEFT JOIN usuarios u ON u.id = a.enviado_por
                          LEFT JOIN chamado_interacoes i ON i.id = a.interacao_id
                         WHERE a.chamado_id = %s {filtro} ORDER BY a.criado_em""", [chamado_id])


@bp.get("/anexos/<int:anexo_id>")
@login_obrigatorio
def baixar(anexo_id):
    a = db().one("""SELECT a.*, c.cliente_id, i.tipo AS tipo_interacao FROM chamado_anexos a
                      JOIN chamados c ON c.id = a.chamado_id
                      LEFT JOIN chamado_interacoes i ON i.id = a.interacao_id
                     WHERE a.id = %s""", [anexo_id]) or abort(404)
    u = g.usuario
    if u["perfil"] == "cliente":
        if a["cliente_id"] != u["cliente_id"] or a["tipo_interacao"] == "interna":
            abort(404)
    elif not pode("chamados"):
        abort(403)
    else:
        from .chamados import visivel
        resp = db().val("SELECT responsavel_id FROM chamados WHERE id = %s", [a["chamado_id"]])
        if not visivel({"responsavel_id": resp}):
            abort(403)
    if a["arquivo"] is None:  # arquivado: depois de 90 dias o arquivo fica só no Google Drive
        if u["perfil"] != "cliente" and a.get("drive_url"):
            from flask import redirect
            return redirect(a["drive_url"])
        from flask import flash, redirect
        flash("Este anexo foi arquivado (mais de 90 dias). Peça uma cópia à equipe da Porto Soluções.", "erro")
        return redirect(request.referrer or "/")
    r = Response(bytes(a["arquivo"]), mimetype="application/octet-stream")
    r.headers["Content-Disposition"] = f'attachment; filename="{a["nome_arquivo"]}"'
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r
