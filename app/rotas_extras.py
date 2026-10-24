"""Horas extras: a Porto solicita, o cliente aprova no portal; aprovadas somam ao saldo do contrato."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .acessos import eh_admin, pode
from .auth import login_obrigatorio, perfis
from .db import db
from .util import TZ, horas_dec, moeda, parse_decimal

bp = Blueprint("extras", __name__)
cliente = perfis("cliente")


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _gerencia():
    return eh_admin() or pode("contratos")


def aprovadores(cliente_id):
    """Usuários do cliente marcados 'Pode aprovar horas extras'; se ninguém estiver marcado, todos os ativos."""
    marcados = db().all("""SELECT id, nome, email FROM usuarios WHERE cliente_id = %s AND perfil = 'cliente'
                            AND ativo AND aprova_horas_extras""", [cliente_id])
    return marcados or db().all("""SELECT id, nome, email FROM usuarios WHERE cliente_id = %s AND perfil = 'cliente'
                                    AND ativo""", [cliente_id])


def _pedido(pid):
    return db().one("""SELECT e.*, ct.descricao AS contrato, ct.cliente_id, ct.tipo::text AS contrato_tipo,
                              coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, c.numero, c.titulo,
                              u.nome AS solicitante, u.email AS solicitante_email, r.nome AS respondente
                         FROM horas_extras e JOIN contratos ct ON ct.id = e.contrato_id
                         JOIN clientes cl ON cl.id = ct.cliente_id
                         LEFT JOIN chamados c ON c.id = e.chamado_id
                         JOIN usuarios u ON u.id = e.solicitado_por
                         LEFT JOIN usuarios r ON r.id = e.respondido_por
                        WHERE e.id = %s""", [pid])


def _email(para, assunto, corpo):
    db().run("INSERT INTO emails_saida (para, assunto, corpo, evento) VALUES (%s,%s,%s,'horas_extras')",
             [para, assunto, corpo])


# =============================================================================
# Equipe
# =============================================================================
@bp.route("/horas-extras", methods=["GET", "POST"])
@login_obrigatorio
def lista():
    if g.usuario["perfil"] == "cliente":
        return redirect(url_for("extras.portal_lista"))
    if request.method == "POST":
        return _solicitar()
    if not _gerencia():
        abort(403)
    f = {"status": request.args.get("status", "pendente"), "cliente": _int(request.args.get("cliente"))}
    where, p = ["true"], []
    if f["status"] in ("pendente", "aprovado", "recusado", "cancelado"):
        where.append("e.status = %s"); p.append(f["status"])
    if f["cliente"]:
        where.append("ct.cliente_id = %s"); p.append(f["cliente"])
    pedidos = db().all(f"""SELECT e.*, ct.descricao AS contrato, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
                                  c.numero, u.nome AS solicitante, r.nome AS respondente
                             FROM horas_extras e JOIN contratos ct ON ct.id = e.contrato_id
                             JOIN clientes cl ON cl.id = ct.cliente_id
                             LEFT JOIN chamados c ON c.id = e.chamado_id
                             JOIN usuarios u ON u.id = e.solicitado_por
                             LEFT JOIN usuarios r ON r.id = e.respondido_por
                            WHERE {' AND '.join(where)} ORDER BY e.solicitado_em DESC LIMIT 300""", p)
    contratos = db().all("""SELECT ct.id, ct.descricao, ct.valor_hora, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
                                   s.horas_saldo, s.pct_consumido
                              FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
                              LEFT JOIN vw_saldo_banco_horas s ON s.contrato_id = ct.id
                             WHERE ct.ativo AND ct.horas_pacote IS NOT NULL ORDER BY cliente, ct.descricao""")
    return render_template("extras/lista.html", pedidos=pedidos, contratos=contratos, f=f,
                           clientes=db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes ORDER BY 2"))


def _solicitar():
    """Pedido feito pela tela de horas extras (gestão) ou de dentro do chamado (quem lança horas)."""
    f = request.form
    numero = _int((f.get("chamado") or "").lstrip("#"))
    chamado = db().one("SELECT id, numero, contrato_id FROM chamados WHERE numero = %s", [numero]) if numero else None
    contrato_id = _int(f.get("contrato_id")) or (chamado and chamado["contrato_id"])
    volta = url_for("equipe.chamado", numero=chamado["numero"]) if chamado and f.get("origem") == "chamado" \
        else url_for("extras.lista")
    if not (_gerencia() or (chamado and pode("horas"))):
        abort(403)
    try:
        ct = db().one("""SELECT ct.*, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
                           FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
                          WHERE ct.id = %s AND ct.ativo""", [contrato_id]) if contrato_id else None
        if not ct or ct["horas_pacote"] is None:
            raise ValueError("Escolha um contrato ativo com horas liberadas.")
        if chamado and chamado["contrato_id"] != ct["id"]:
            raise ValueError("O chamado não pertence a esse contrato.")
        horas = parse_decimal(f.get("horas"))
        if not horas or horas <= 0 or horas > 1000:
            raise ValueError("Informe a quantidade de horas extras.")
        valor = parse_decimal(f.get("valor_hora")) if f.get("valor_hora") else ct["valor_hora"]
        just = (f.get("justificativa") or "").strip()
        if len(just) < 10:
            raise ValueError("Explique ao cliente por que as horas extras são necessárias (mín. 10 caracteres).")
        if db().val("SELECT 1 FROM horas_extras WHERE contrato_id = %s AND status = 'pendente' AND "
                    "chamado_id IS NOT DISTINCT FROM %s", [ct["id"], chamado and chamado["id"]]):
            raise ValueError("Já existe um pedido pendente para este contrato/chamado aguardando o cliente.")
        pid = db().val("""INSERT INTO horas_extras (contrato_id, chamado_id, horas, valor_hora, justificativa, solicitado_por)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                       [ct["id"], chamado and chamado["id"], horas, valor, just, g.usuario["id"]])
        link = url_for("extras.portal_lista", _external=True)
        total = f" (total estimado {moeda(horas * valor)})" if valor else ""
        for u in aprovadores(ct["cliente_id"]):
            _email(u["email"], f"Porto Soluções – aprovação de {horas_dec(horas)} extras ({ct['descricao']})",
                   f"Olá, {u['nome'].split()[0]}.\n\nA Porto Soluções solicita {horas_dec(horas)} extras no contrato "
                   f"{ct['descricao']}{total}"
                   + (f", para o chamado #{chamado['numero']}" if chamado else "") +
                   f".\n\nMotivo: {just}\n\nAprove ou recuse pelo portal: {link}\n\n—\nPorto Soluções Corporativas")
        from .integracoes import chat
        chat(f"🕒 Pedido de {horas_dec(horas)} extras enviado a {ct['cliente']} ({ct['descricao']}) por {g.usuario['nome']}.")
        flash(f"Pedido de {horas_dec(horas)} extras enviado ao cliente para aprovação no portal.", "ok")
        if not pid:
            raise ValueError("Falha ao registrar o pedido.")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(volta)


@bp.post("/horas-extras/<int:pid>/cancelar")
@login_obrigatorio
def cancelar(pid):
    e = _pedido(pid) or abort(404)
    if g.usuario["perfil"] == "cliente" or not (_gerencia() or e["solicitado_por"] == g.usuario["id"]):
        abort(403)
    if e["status"] == "pendente":
        db().run("UPDATE horas_extras SET status = 'cancelado', respondido_em = now() WHERE id = %s", [pid])
        flash("Pedido cancelado.", "ok")
    return redirect(request.referrer or url_for("extras.lista"))


# =============================================================================
# Portal do cliente
# =============================================================================
@bp.get("/portal/horas-extras")
@cliente
def portal_lista():
    cid = g.usuario["cliente_id"]
    pedidos = db().all("""SELECT e.*, ct.descricao AS contrato, c.numero, c.titulo, u.nome AS solicitante,
                                 r.nome AS respondente
                            FROM horas_extras e JOIN contratos ct ON ct.id = e.contrato_id
                            LEFT JOIN chamados c ON c.id = e.chamado_id
                            JOIN usuarios u ON u.id = e.solicitado_por
                            LEFT JOIN usuarios r ON r.id = e.respondido_por
                           WHERE ct.cliente_id = %s AND e.status <> 'cancelado'
                           ORDER BY (e.status = 'pendente') DESC, e.solicitado_em DESC""", [cid])
    pode_aprovar = g.usuario["id"] in {u["id"] for u in aprovadores(cid)}
    return render_template("extras/portal.html", pedidos=pedidos, pode_aprovar=pode_aprovar)


@bp.post("/portal/horas-extras/<int:pid>")
@cliente
def portal_responder(pid):
    e = _pedido(pid)
    if not e or e["cliente_id"] != g.usuario["cliente_id"]:
        abort(404)
    if g.usuario["id"] not in {u["id"] for u in aprovadores(e["cliente_id"])}:
        abort(403)
    acao = request.form.get("acao")
    resposta = (request.form.get("resposta") or "").strip() or None
    if e["status"] != "pendente" or acao not in ("aprovado", "recusado"):
        flash("Este pedido já foi respondido.", "erro")
        return redirect(url_for("extras.portal_lista"))
    if acao == "recusado" and not resposta:
        flash("Conte para a Porto o motivo da recusa.", "erro")
        return redirect(url_for("extras.portal_lista"))
    mes = dt.datetime.now(TZ).date().replace(day=1)
    db().run("""UPDATE horas_extras SET status = %s, respondido_por = %s, respondido_em = now(), resposta = %s,
                       mes = %s WHERE id = %s AND status = 'pendente'""",
             [acao, g.usuario["id"], resposta, mes, pid])
    if acao == "aprovado":  # saldo cresceu: os avisos de 80%/100% podem disparar de novo
        db().run("DELETE FROM saldo_alertas WHERE contrato_id = %s", [e["contrato_id"]])
    verbo = "aprovou" if acao == "aprovado" else "recusou"
    texto = (f"{g.usuario['nome']} ({e['cliente']}) {verbo} o pedido de {horas_dec(e['horas'])} extras no contrato "
             f"{e['contrato']}" + (f" (chamado #{e['numero']})" if e["numero"] else "") + "."
             + (f"\n\nComentário: {resposta}" if resposta else "")
             + ("\n\nAs horas já foram somadas ao saldo"
                + (" deste mês." if e["contrato_tipo"] == "mensal_fixo" else " do contrato.") if acao == "aprovado" else ""))
    destino = {e["solicitante_email"]}
    resp = db().val("""SELECT u.email FROM clientes cl JOIN usuarios u ON u.id = cl.responsavel_id
                        WHERE cl.id = %s""", [e["cliente_id"]])
    if resp:
        destino.add(resp)
    for email in destino:
        _email(email, f"SisPorto – horas extras {'aprovadas' if acao == 'aprovado' else 'recusadas'} "
                      f"({e['cliente']})", texto + "\n\n—\nSisPorto")
    from .integracoes import chat
    chat(("✅ " if acao == "aprovado" else "❌ ") + texto.split("\n")[0])
    flash("Obrigado! Pedido aprovado: as horas já estão disponíveis." if acao == "aprovado" else
          "Pedido recusado. A Porto Soluções foi avisada.", "ok")
    return redirect(url_for("extras.portal_lista"))
