"""Integrações: Google Chat (alertas), tarefas periódicas (SLA, NPS), Agenda, Drive e BI.

As rotas /integracao/* usam o cabeçalho X-Token e são chamadas pelo Apps Script
da conta suporte@ (infra/gmail/sisporto-email.gs) a cada 5 minutos.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import secrets

import requests
from flask import Blueprint, abort, current_app, jsonify, request, url_for

from .db import db
from .util import TZ, data_hora

bp = Blueprint("integracoes", __name__)
log = logging.getLogger(__name__)


# --- configurações (tabela configuracoes) ------------------------------------------
def config(chave, padrao=None):
    v = db().val("SELECT texto FROM configuracoes WHERE chave = %s", [chave])
    return v if v is not None else padrao


def salvar_config(chave, valor):
    db().run("""INSERT INTO configuracoes (chave, texto) VALUES (%s, %s)
                ON CONFLICT (chave) DO UPDATE SET texto = EXCLUDED.texto, atualizado_em = now()""", [chave, valor])


def _autorizar():
    token = current_app.config.get("INTEGRACAO_TOKEN") or ""
    if not token:
        abort(404)
    if not secrets.compare_digest(request.headers.get("X-Token") or "", token):
        abort(403)


# --- Google Chat ------------------------------------------------------------------
def chat(texto: str) -> bool:
    url = config("chat_webhook")
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": texto}, timeout=5)
        return r.status_code < 300
    except requests.RequestException as e:  # alerta nunca derruba a operação
        log.warning("Falha ao enviar alerta ao Google Chat: %s", e)
        return False


def _link_chamado(numero):
    try:
        return url_for("equipe.chamado", numero=numero, _external=True)
    except RuntimeError:
        return ""


def alerta_critico(chamado_id):
    c = db().one("SELECT * FROM vw_chamados_sla WHERE id = %s", [chamado_id])
    if c:
        chat(f"🚨 *Chamado CRÍTICO #{c['numero']}* – {c['cliente_nome']}\n{c['titulo']}\n"
             f"Prazo de solução: {data_hora(c['prazo_solucao'])}\n{_link_chamado(c['numero'])}")


def verificar_sla():
    """Avisa uma vez quando o chamado entra em risco e uma vez quando estoura."""
    n = 0
    for c in db().all("""SELECT * FROM vw_chamados_sla
                          WHERE status IN ('novo','em_atendimento') AND situacao_sla IN ('em_risco','estourado')"""):
        if c["situacao_sla"] == "estourado" and not c["alerta_estouro_em"]:
            chat(f"⛔ *SLA ESTOURADO* – chamado #{c['numero']} ({c['prioridade']}) – {c['cliente_nome']}\n"
                 f"{c['titulo']}\nResponsável: {c['responsavel_nome'] or 'ninguém'}\n{_link_chamado(c['numero'])}")
            db().run("UPDATE chamados SET alerta_estouro_em = now(), alerta_risco_em = coalesce(alerta_risco_em, now()) "
                     "WHERE id = %s", [c["id"]])
            n += 1
        elif c["situacao_sla"] == "em_risco" and not c["alerta_risco_em"]:
            chat(f"⚠️ *SLA em risco* – chamado #{c['numero']} ({c['prioridade']}) – {c['cliente_nome']}\n"
                 f"{c['titulo']}\nVence em {data_hora(c['prazo_solucao'])} · Responsável: "
                 f"{c['responsavel_nome'] or 'ninguém'}\n{_link_chamado(c['numero'])}")
            db().run("UPDATE chamados SET alerta_risco_em = now() WHERE id = %s", [c["id"]])
            n += 1
    return n


# --- NPS ---------------------------------------------------------------------------
def _hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def enviar_nps(forcar=False):
    """No dia configurado de cada mês, envia a pesquisa a quem tem 'Recebe NPS'."""
    if config("nps_ativo", "0") != "1" and not forcar:
        return 0
    hoje = dt.datetime.now(TZ).date()
    dia = int(config("nps_dia", "1") or 1)
    if hoje.day < dia and not forcar:
        return 0
    mes = hoje.replace(day=1)
    n = 0
    for u in db().all("""SELECT u.id, u.nome, u.email, u.cliente_id FROM usuarios u JOIN clientes c ON c.id = u.cliente_id
                          WHERE u.perfil = 'cliente' AND u.ativo AND u.recebe_nps AND c.ativo
                            AND NOT EXISTS (SELECT 1 FROM nps_respostas n WHERE n.usuario_id = u.id AND n.mes = %s)""",
                      [mes]):
        token = secrets.token_urlsafe(24)
        db().run("""INSERT INTO nps_respostas (usuario_id, cliente_id, mes, token_hash) VALUES (%s,%s,%s,%s)""",
                 [u["id"], u["cliente_id"], mes, _hash(token)])
        link = url_for("integracoes.nps", token=token, _external=True)
        corpo = (f"Olá, {u['nome']}.\n\nSua opinião é muito importante para nós. Em uma escala de 0 a 10, quanto "
                 f"você recomendaria a Porto Soluções a um colega ou parceiro?\n\nResponda com um clique: {link}\n\n"
                 "Leva menos de 1 minuto. Obrigado!\n\n—\nPorto Soluções Corporativas")
        db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento)
                    VALUES (%s, 'Porto Soluções – como estamos indo?', %s, 'nps')""", [u["email"], corpo])
        n += 1
    return n


@bp.route("/nps/<token>", methods=["GET", "POST"])
def nps(token):
    from flask import render_template
    r = db().one("""SELECT n.*, u.nome FROM nps_respostas n JOIN usuarios u ON u.id = n.usuario_id
                     WHERE n.token_hash = %s AND n.enviado_em > now() - interval '45 days'""", [_hash(token)])
    if not r:
        abort(404)
    if request.method == "POST" and r["respondido_em"] is None:
        nota = request.form.get("nota", "")
        if nota.isdigit() and 0 <= int(nota) <= 10:
            db().run("UPDATE nps_respostas SET nota = %s, comentario = %s, respondido_em = now() WHERE id = %s",
                     [int(nota), (request.form.get("comentario") or "").strip()[:2000] or None, r["id"]])
            r = {**r, "respondido_em": True}
    return render_template("nps.html", r=r)


# --- rotas chamadas pelo Apps Script --------------------------------------------------
@bp.post("/integracao/tarefas")
def tarefas():
    _autorizar()
    from .alertas import verificar_alertas_saldo
    return jsonify(alertas_sla=verificar_sla(), nps_enviados=enviar_nps(), alertas_saldo=verificar_alertas_saldo())


@bp.post("/integracao/agenda")
def agenda_pendentes():
    _autorizar()
    itens = db().all("""SELECT a.id, a.status, a.tipo, a.inicio, a.fim, a.local, a.descricao, a.convidados,
                               a.google_evento_id, c.numero, c.titulo, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
                               u.nome AS recurso
                          FROM agendamentos a JOIN chamados c ON c.id = a.chamado_id
                          JOIN clientes cl ON cl.id = c.cliente_id JOIN usuarios u ON u.id = a.recurso_id
                         WHERE a.status IN ('pendente', 'cancelar') ORDER BY a.id LIMIT 20""")
    for i in itens:
        i["inicio"], i["fim"] = i["inicio"].isoformat(), i["fim"].isoformat()
        i["titulo_evento"] = (f"{'Visita' if i['tipo'] == 'visita' else 'Reunião'} – {i['cliente']} – "
                              f"chamado #{i['numero']}")
        i["link"] = _link_chamado(i["numero"])
    return jsonify(itens=itens)


@bp.post("/integracao/agenda/ok")
def agenda_ok():
    _autorizar()
    d = request.get_json(silent=True) or {}
    if d.get("acao") == "criado":
        db().run("UPDATE agendamentos SET status = 'criado', google_evento_id = %s WHERE id = %s AND status = 'pendente'",
                 [d.get("evento_id"), int(d.get("id", 0))])
    elif d.get("acao") == "cancelado":
        db().run("UPDATE agendamentos SET status = 'cancelado' WHERE id = %s AND status = 'cancelar'",
                 [int(d.get("id", 0))])
    return jsonify(ok=True)


@bp.post("/integracao/drive")
def drive_pendente():
    """Entrega UM anexo ainda não copiado ao Drive (até 20 MB, em base64)."""
    _autorizar()
    a = db().one("""SELECT a.id, a.nome_arquivo, a.tipo_mime, a.arquivo, c.numero, c.titulo, c.drive_pasta_url,
                           coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
                      FROM chamado_anexos a JOIN chamados c ON c.id = a.chamado_id JOIN clientes cl ON cl.id = c.cliente_id
                     WHERE a.drive_arquivo_id IS NULL AND a.arquivo IS NOT NULL ORDER BY a.id LIMIT 1""")
    if not a:
        return jsonify(anexo=None)
    a["conteudo"] = base64.b64encode(bytes(a.pop("arquivo"))).decode()
    return jsonify(anexo=a)


@bp.post("/integracao/drive/ok")
def drive_ok():
    _autorizar()
    d = request.get_json(silent=True) or {}
    aid = int(d.get("anexo_id", 0))
    db().run("UPDATE chamado_anexos SET drive_arquivo_id = %s, drive_url = %s WHERE id = %s",
             [d.get("arquivo_id"), d.get("url"), aid])
    if d.get("pasta_url"):
        db().run("""UPDATE chamados SET drive_pasta_url = %s
                     WHERE id = (SELECT chamado_id FROM chamado_anexos WHERE id = %s)""", [d["pasta_url"], aid])
    return jsonify(ok=True)


@bp.post("/integracao/bi")
def bi():
    """Dados para a planilha 'SisPorto BI' (fonte do Looker Studio)."""
    _autorizar()
    linhas = db().all("""SELECT * FROM vw_bi_apontamentos WHERE data >= current_date - interval '24 months'
                          ORDER BY data, id""")
    contratos = db().all("""SELECT ct.id, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, cl.segmento,
                                   ct.descricao, ct.tipo::text AS tipo, ct.valor_mensal, ct.franquia_horas, ct.valor_hora,
                                   ct.horas_pacote AS horas_liberadas, ct.data_inicio, ct.data_fim, ct.ativo
                              FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id ORDER BY cliente""")
    chamados = db().all("""SELECT numero, criado_em::date AS aberto_em, resolvido_em::date AS resolvido_em, cliente_nome AS cliente,
                                  prioridade::text, status::text, origem::text, categoria, situacao_sla, avaliacao,
                                  responsavel_nome AS responsavel
                             FROM vw_chamados_sla WHERE criado_em >= now() - interval '24 months' ORDER BY numero""")

    def serial(rows):
        out = []
        for r in rows:
            out.append([v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else
                        (float(v) if v.__class__.__name__ == "Decimal" else v) for v in r.values()])
        return out
    return jsonify(abas={
        "apontamentos": {"colunas": list(linhas[0].keys()) if linhas else [], "linhas": serial(linhas)},
        "contratos": {"colunas": list(contratos[0].keys()) if contratos else [], "linhas": serial(contratos)},
        "chamados": {"colunas": list(chamados[0].keys()) if chamados else [], "linhas": serial(chamados)},
    })
