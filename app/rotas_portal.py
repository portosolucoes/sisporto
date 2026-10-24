"""Portal do cliente: painel de horas, chamados e conversa com a equipe."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from . import chamados as ch
from .auth import perfis
from .db import db
from .email_integracao import notificar
from .util import TZ

bp = Blueprint("portal", __name__)
cliente = perfis("cliente")


def _cid():
    return g.usuario["cliente_id"]


@bp.get("/")
@cliente
def painel():
    cid = _cid()
    hoje = dt.datetime.now(TZ).date()
    mes = hoje.replace(day=1)
    empresa = db().one("SELECT * FROM clientes WHERE id = %s", [cid])
    horas_mes = db().one("""SELECT coalesce(sum(minutos_total),0) AS total,
                                   coalesce(sum(minutos_faturaveis_aprovados),0) AS aprovadas,
                                   coalesce(sum(chamados_atendidos),0) AS chamados
                              FROM vw_horas_mes WHERE cliente_id = %s AND mes = %s""", [cid, mes])
    historico = db().all("""SELECT mes, sum(minutos_total) AS minutos FROM vw_horas_mes
                             WHERE cliente_id = %s AND mes > %s GROUP BY mes ORDER BY mes""",
                         [cid, (mes - dt.timedelta(days=170)).replace(day=1)])
    bancos = db().all("""SELECT * FROM vw_saldo_banco_horas s JOIN contratos c ON c.id = s.contrato_id
                          WHERE s.cliente_id = %s AND c.ativo""", [cid])
    franquias = db().all("""SELECT f.*, c.descricao FROM contratos c
                              LEFT JOIN vw_franquia_mes f ON f.contrato_id = c.id AND f.mes = %s
                             WHERE c.cliente_id = %s AND c.ativo AND c.tipo = 'mensal_fixo'
                               AND c.franquia_horas IS NOT NULL""", [mes, cid])
    abertos = db().all("""SELECT * FROM vw_chamados_sla WHERE cliente_id = %s
                           AND status IN ('novo','em_atendimento','aguardando_cliente')
                           ORDER BY criado_em DESC""", [cid])
    return render_template("portal/painel.html", empresa=empresa, horas_mes=horas_mes, historico=historico,
                           bancos=bancos, franquias=franquias, abertos=abertos, mes=mes)


@bp.get("/chamados")
@cliente
def chamados():
    situacao = request.args.get("situacao", "abertos")
    sql = "SELECT * FROM vw_chamados_sla WHERE cliente_id = %s"
    if situacao == "abertos":
        sql += " AND status IN ('novo','em_atendimento','aguardando_cliente')"
    elif situacao == "encerrados":
        sql += " AND status IN ('resolvido','fechado')"
    lista = db().all(sql + " ORDER BY criado_em DESC LIMIT 200", [_cid()])
    return render_template("portal/chamados.html", lista=lista, situacao=situacao)


@bp.route("/chamados/novo", methods=["GET", "POST"])
@cliente
def chamado_novo():
    if request.method == "POST":
        try:
            novo = ch.criar(cliente_id=_cid(), solicitante_id=g.usuario["id"], origem="portal",
                            titulo=request.form.get("titulo"), descricao=request.form.get("descricao"),
                            prioridade=request.form.get("prioridade", "media"),
                            categoria=request.form.get("categoria"))
            notificar(novo["id"], "aberto")
            flash(f"Chamado #{novo['numero']} aberto. Você receberá as atualizações por aqui e por e-mail.", "ok")
            return redirect(url_for("portal.chamado", numero=novo["numero"]))
        except ValueError as e:
            flash(str(e), "erro")
    return render_template("portal/chamado_novo.html", form=request.form, prioridades=ch.PRIORIDADES)


@bp.get("/chamados/<int:numero>")
@cliente
def chamado(numero):
    c = ch.buscar(numero, cliente_id=_cid())
    tempo = ch.linha_do_tempo(c["id"], incluir_internas=False)
    horas = db().all("""SELECT data, minutos, descricao FROM apontamentos
                         WHERE chamado_id = %s AND status <> 'rejeitado' AND faturavel ORDER BY data""", [c["id"]])
    return render_template("portal/chamado.html", c=c, tempo=tempo, horas=horas)


@bp.post("/chamados/<int:numero>/responder")
@cliente
def chamado_responder(numero):
    c = ch.buscar(numero, cliente_id=_cid())
    if c["status"] == "fechado":
        flash("Este chamado está fechado. Abra um novo, se precisar.", "erro")
    else:
        try:
            ch.responder(c["id"], g.usuario["id"], request.form.get("texto"))
            if c["status"] == "resolvido" and request.form.get("reabrir") == "1":
                db().run("UPDATE chamados SET status = 'em_atendimento' WHERE id = %s", [c["id"]])
            flash("Mensagem enviada.", "ok")
        except ValueError as e:
            flash(str(e), "erro")
    return redirect(url_for("portal.chamado", numero=numero) + "#conversa")


@bp.post("/chamados/<int:numero>/avaliar")
@cliente
def chamado_avaliar(numero):
    c = ch.buscar(numero, cliente_id=_cid())
    nota = request.form.get("nota", "")
    if c["status"] in ("resolvido", "fechado") and nota in ("1", "2", "3", "4", "5"):
        db().run("""UPDATE chamados SET avaliacao = %s, avaliacao_comentario = %s,
                           status = 'fechado' WHERE id = %s""",
                 [int(nota), (request.form.get("comentario") or "").strip() or None, c["id"]])
        flash("Obrigado pela avaliação!", "ok")
    return redirect(url_for("portal.chamado", numero=numero))


@bp.get("/horas")
@cliente
def horas():
    hoje = dt.datetime.now(TZ).date()
    try:
        mes = dt.date.fromisoformat((request.args.get("mes") or "") + "-01")
    except ValueError:
        mes = hoje.replace(day=1)
    prox = (mes + dt.timedelta(days=32)).replace(day=1)
    linhas = db().all("""
        SELECT a.data, a.minutos, a.descricao, a.faturavel, a.status, c.numero, c.titulo, u.nome AS tecnico,
               ct.descricao AS contrato
          FROM apontamentos a
          JOIN contratos ct ON ct.id = a.contrato_id
          LEFT JOIN chamados c ON c.id = a.chamado_id
          JOIN usuarios u ON u.id = a.usuario_id
         WHERE ct.cliente_id = %s AND a.data >= %s AND a.data < %s AND a.status <> 'rejeitado'
         ORDER BY a.data, c.numero""", [_cid(), mes, prox])
    total = sum(l["minutos"] for l in linhas)
    fat = sum(l["minutos"] for l in linhas if l["faturavel"])
    meses = db().all("""SELECT DISTINCT to_char(mes, 'YYYY-MM') AS m FROM vw_horas_mes
                         WHERE cliente_id = %s ORDER BY 1 DESC""", [_cid()])
    return render_template("portal/horas.html", linhas=linhas, total=total, faturaveis=fat, mes=mes,
                           meses=[m["m"] for m in meses])
