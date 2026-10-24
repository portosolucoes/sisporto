"""Contratos: rotina própria (lista com filtros, inclusão escolhendo o cliente, edição)."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .acessos import requer
from .db import IntegrityError, db
from .rotas_equipe import CAMPOS_CONTRATO, _dados_contrato, _int, _politicas, _tipos_servico
from .util import TZ

bp = Blueprint("contratos", __name__)


@bp.get("/contratos")
@requer("contratos")
def lista():
    f = {k: request.args.get(k, "") for k in ("cliente", "tipo", "situacao", "busca")}
    f["situacao"] = f["situacao"] or "ativos"
    hoje = dt.datetime.now(TZ).date()
    where, p = ["true"], []
    if _int(f["cliente"]):
        where.append("ct.cliente_id = %s"); p.append(int(f["cliente"]))
    if f["tipo"]:
        where.append("ct.tipo::text = %s"); p.append(f["tipo"])
    if f["situacao"] == "ativos":
        where.append("ct.ativo")
    elif f["situacao"] == "inativos":
        where.append("NOT ct.ativo")
    elif f["situacao"] == "vencendo":
        where.append("ct.ativo AND ct.data_fim BETWEEN %s AND %s"); p += [hoje, hoje + dt.timedelta(days=60)]
    elif f["situacao"] == "sem_saldo":
        where.append("ct.ativo AND s.horas_saldo <= 0")
    if f["busca"].strip():
        where.append("(ct.descricao ILIKE %s OR cl.razao_social ILIKE %s OR cl.nome_fantasia ILIKE %s)")
        p += [f"%{f['busca'].strip()}%"] * 3
    linhas = db().all(f"""
        SELECT ct.*, ct.tipo::text AS tipo, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
               sp.nome AS sla, ts.nome AS tipo_servico, s.horas_saldo, s.pct_consumido,
               (ct.data_fim IS NOT NULL AND ct.data_fim < %s) AS vencido
          FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
          LEFT JOIN sla_politicas sp ON sp.id = ct.sla_politica_id
          LEFT JOIN tipos_servico ts ON ts.id = ct.tipo_servico_id
          LEFT JOIN vw_saldo_banco_horas s ON s.contrato_id = ct.id
         WHERE {' AND '.join(where)}
         ORDER BY cliente, ct.ativo DESC, ct.data_inicio DESC""", [hoje] + p)
    receita = sum((l["valor_mensal"] or 0) for l in linhas if l["ativo"])
    return render_template("contratos/lista.html", linhas=linhas, f=f, receita=receita,
                           clientes=db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes "
                                             "ORDER BY ativo DESC, 2"))


@bp.route("/contratos/novo", methods=["GET", "POST"])
@requer("contratos")
def novo():
    clientes = db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes WHERE ativo ORDER BY 2")
    if request.method == "POST":
        cid = _int(request.form.get("cliente_id"))
        try:
            if not cid or not db().val("SELECT 1 FROM clientes WHERE id = %s AND ativo", [cid]):
                raise ValueError("Escolha o cliente (ativo).")
            v = _dados_contrato(request.form)
            novo_id = db().val(f"""INSERT INTO contratos (cliente_id, {', '.join(CAMPOS_CONTRATO)})
                                   VALUES (%s, {', '.join(['%s'] * len(CAMPOS_CONTRATO))}) RETURNING id""",
                               [cid] + [v[k] for k in CAMPOS_CONTRATO])
            flash("Contrato incluído.", "ok")
            return redirect(url_for("contratos.lista", cliente=cid) + f"#ct{novo_id}")
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Dados do contrato inconsistentes. Confira os valores.", "erro")
    return render_template("equipe/contrato_form.html", cliente=None, clientes=clientes, ct=request.form, novo=True,
                           politicas=_politicas(), tipos=_tipos_servico(),
                           voltar=url_for("contratos.lista"), cliente_pre=_int(request.args.get("cliente")))
