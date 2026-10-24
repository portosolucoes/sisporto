"""Chamados por e-mail e notificações.

O envio e a leitura da caixa suporte@ são feitos por um Google Apps Script
(infra/gmail/sisporto-email.gs) rodando na conta do Workspace. Ele conversa com
estas rotas usando o cabeçalho X-Token (segredo INTEGRACAO_TOKEN):

  POST /integracao/email/entrada   e-mail recebido  -> novo chamado ou resposta
  POST /integracao/email/saida     devolve até 20 e-mails pendentes para enviar
  POST /integracao/email/enviados  confirma os ids enviados
"""
from __future__ import annotations

import re
import secrets

from flask import Blueprint, abort, current_app, jsonify, request, url_for

from . import chamados as ch
from .db import db
from .util import data_hora

bp = Blueprint("email", __name__)

RE_NUMERO = re.compile(r"\[#(\d+)\]")
RE_CITACAO = re.compile(
    r"^(?:Em .{5,120} escreveu:|On .{5,120} wrote:|De: .+|From: .+|-----Original Message-----|_{10,})\s*$",
    re.IGNORECASE | re.MULTILINE)


def _autorizar():
    token = current_app.config.get("INTEGRACAO_TOKEN") or ""
    enviado = request.headers.get("X-Token") or ""
    if not token:
        abort(404)
    if not secrets.compare_digest(enviado, token):
        abort(403)


def limpar_resposta(texto: str) -> str:
    """Remove o histórico citado de uma resposta de e-mail."""
    texto = (texto or "").replace("\r\n", "\n")
    m = RE_CITACAO.search(texto)
    if m:
        texto = texto[:m.start()]
    linhas = [l for l in texto.split("\n") if not l.startswith(">")]
    return "\n".join(linhas).strip()


# =============================================================================
# Fila de saída (notificações)
# =============================================================================
def _destinatario(c):
    if c.get("solicitante_id"):
        u = db().one("SELECT email, perfil, ativo FROM usuarios WHERE id = %s", [c["solicitante_id"]])
        if u and u["perfil"] == "cliente" and u["ativo"]:
            return u["email"]
        return None  # chamado aberto pela equipe em nome próprio
    return c.get("solicitante_email")


def _link(numero):
    try:
        return url_for("portal.chamado", numero=numero, _external=True)
    except RuntimeError:
        return ""


def notificar(chamado_id: int, evento: str, texto: str | None = None, autor_id: int | None = None):
    """Enfileira e-mail ao solicitante. evento: aberto | resposta | resolvido."""
    c = db().one("SELECT * FROM vw_chamados_sla WHERE id = %s", [chamado_id])
    if not c:
        return
    para = _destinatario(c)
    if not para or (autor_id and autor_id == c.get("solicitante_id")):
        return
    assunto = f"[#{c['numero']}] {c['titulo']}"
    link = _link(c["numero"])
    acesso = f"\n\nAcompanhe pelo portal: {link}" if link and c.get("solicitante_id") else ""
    rodape = ("\n\n—\nSuporte Porto Soluções Corporativas\n"
              "Responda este e-mail mantendo o [#número] no assunto para continuar a conversa.")
    if evento == "aberto":
        corpo = (f"Olá,\n\nRecebemos seu chamado #{c['numero']}: {c['titulo']}.\n"
                 f"Prioridade: {c['prioridade']}. Previsão de solução: {data_hora(c['prazo_solucao'])}.{acesso}")
    elif evento == "resposta":
        corpo = f"Nova mensagem da equipe no chamado #{c['numero']}:\n\n{texto or ''}{acesso}"
    elif evento == "resolvido":
        corpo = (f"O chamado #{c['numero']} foi marcado como resolvido.\n\n"
                 f"Se o problema continuar, responda este e-mail que reabrimos.{acesso}")
    else:
        return
    db().run("""INSERT INTO emails_saida (chamado_id, para, assunto, corpo, evento)
                VALUES (%s,%s,%s,%s,%s)""", [chamado_id, para, assunto, corpo + rodape, evento])


# =============================================================================
# Rotas da integração
# =============================================================================
@bp.post("/entrada")
def entrada():
    _autorizar()
    d = request.get_json(silent=True) or {}
    msg_id = (d.get("message_id") or "").strip()
    thread_id = (d.get("thread_id") or "").strip() or None
    de = (d.get("de") or "").strip().lower()
    assunto = (d.get("assunto") or "").strip()
    texto = d.get("texto") or ""
    if not msg_id or "@" not in de:
        abort(400)

    # já processado?
    if db().val("""SELECT 1 FROM chamado_interacoes WHERE email_message_id = %s
                   UNION ALL SELECT 1 FROM chamados WHERE email_message_id = %s
                   UNION ALL SELECT 1 FROM emails_recusados WHERE message_id = %s""", [msg_id, msg_id, msg_id]):
        return jsonify(acao="duplicado")

    autor = db().one("SELECT id, perfil, cliente_id, ativo FROM usuarios WHERE email = %s", [de])

    # resposta a chamado existente: pelo [#número] no assunto ou pela conversa do Gmail
    alvo = None
    m = RE_NUMERO.search(assunto)
    if m:
        alvo = db().one("SELECT id, numero, cliente_id, status FROM chamados WHERE numero = %s", [int(m.group(1))])
    if not alvo and thread_id:
        alvo = db().one("SELECT id, numero, cliente_id, status FROM chamados WHERE email_thread_id = %s",
                        [thread_id])
    if alvo:
        dominio = de.split("@")[1]
        cliente_dom = db().val("SELECT dominio_email FROM clientes WHERE id = %s", [alvo["cliente_id"]])
        permitido = (autor and autor["ativo"] and (autor["perfil"] != "cliente"
                                                   or autor["cliente_id"] == alvo["cliente_id"])) \
            or (cliente_dom and dominio == cliente_dom)
        if not permitido:
            return _recusar(msg_id, de, assunto, "remetente não pertence ao cliente do chamado")
        corpo = limpar_resposta(texto) or "(mensagem sem texto)"
        db().run("""INSERT INTO chamado_interacoes (chamado_id, autor_id, autor_email, tipo, texto, email_message_id)
                    VALUES (%s,%s,%s,'publica',%s,%s)""",
                 [alvo["id"], autor["id"] if autor else None, None if autor else de, corpo, msg_id])
        if alvo["status"] == "resolvido" and not (autor and autor["perfil"] != "cliente"):
            db().run("UPDATE chamados SET status = 'em_atendimento' WHERE id = %s", [alvo["id"]])
        if autor and autor["perfil"] != "cliente":
            notificar(alvo["id"], "resposta", corpo, autor["id"])
        return jsonify(acao="resposta", numero=alvo["numero"])

    # novo chamado
    if autor and autor["perfil"] != "cliente":
        return _recusar(msg_id, de, assunto, "e-mail da equipe sem [#número] no assunto")
    if autor and autor["ativo"]:
        cliente_id, solicitante_id, solicitante_email = autor["cliente_id"], autor["id"], None
    else:
        cliente_id = db().val("SELECT id FROM clientes WHERE dominio_email = %s AND ativo", [de.split("@")[1]])
        solicitante_id, solicitante_email = None, de
    if not cliente_id:
        return _recusar(msg_id, de, assunto, "remetente desconhecido")
    novo = ch.criar(cliente_id=cliente_id, solicitante_id=solicitante_id, solicitante_email=solicitante_email,
                    titulo=(assunto or "(sem assunto)")[:200], descricao=limpar_resposta(texto) or "(sem texto)",
                    prioridade="media", origem="email")
    db().run("UPDATE chamados SET email_message_id = %s, email_thread_id = %s WHERE id = %s",
             [msg_id, thread_id, novo["id"]])
    notificar(novo["id"], "aberto")
    return jsonify(acao="novo", numero=novo["numero"])


def _recusar(msg_id, de, assunto, motivo):
    db().run("""INSERT INTO emails_recusados (message_id, de, assunto, motivo) VALUES (%s,%s,%s,%s)
                ON CONFLICT (message_id) DO NOTHING""", [msg_id, de, assunto, motivo])
    return jsonify(acao="recusado", motivo=motivo)


@bp.post("/saida")
def saida():
    _autorizar()
    pend = db().all("""SELECT id, para, assunto, corpo FROM emails_saida
                        WHERE enviado_em IS NULL ORDER BY id LIMIT 20""")
    return jsonify(emails=pend)


@bp.post("/enviados")
def enviados():
    _autorizar()
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", []) if str(i).isdigit()]
    n = 0
    for i in ids:
        n += db().run("UPDATE emails_saida SET enviado_em = now() WHERE id = %s AND enviado_em IS NULL", [i])
    return jsonify(confirmados=n)
