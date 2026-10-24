"""Telas da equipe interna: painel, chamados, clientes, contratos, equipe e horas."""
from __future__ import annotations

import datetime as dt
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import anexos as anx
from . import chamados as ch
from .acessos import ROTINAS, SO_ADMIN, eh_admin, eh_master, padrao, pode, requer, salvar_permissoes
from .auth import admin
from .auth import equipe
from .db import IntegrityError, db
from .email_integracao import notificar
from .util import (TZ, cnpj_valido, normalizar_dominio, parse_decimal, parse_duracao, so_digitos,
                   senha_temporaria)

bp = Blueprint("equipe", __name__)


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _hoje():
    return dt.datetime.now(TZ).date()


# =============================================================================
# Painel
# =============================================================================
@bp.get("/painel")
@equipe
def painel():
    uid = g.usuario["id"]
    fv, pv = ch.filtro_visivel()
    resumo = db().one(f"""
        SELECT count(*) FILTER (WHERE status IN ('novo','em_atendimento','aguardando_cliente')) AS abertos,
               count(*) FILTER (WHERE situacao_sla = 'estourado')  AS estourados,
               count(*) FILTER (WHERE situacao_sla = 'em_risco')   AS em_risco,
               count(*) FILTER (WHERE status = 'aguardando_cliente') AS aguardando,
               count(*) FILTER (WHERE responsavel_id IS NULL AND status IN ('novo','em_atendimento')) AS sem_responsavel
          FROM vw_chamados_sla WHERE {fv}""", pv)
    meus = db().all("""
        SELECT * FROM vw_chamados_sla
         WHERE responsavel_id = %s AND status IN ('novo','em_atendimento','aguardando_cliente')
         ORDER BY prazo_solucao NULLS LAST LIMIT 15""", [uid])
    fila = db().all("""
        SELECT * FROM vw_chamados_sla
         WHERE responsavel_id IS NULL AND status IN ('novo','em_atendimento')
         ORDER BY prazo_solucao NULLS LAST LIMIT 10""")
    inicio_semana = _hoje() - dt.timedelta(days=_hoje().weekday())
    minhas_horas = db().val("""SELECT coalesce(sum(minutos),0) FROM apontamentos
                                WHERE usuario_id = %s AND data >= %s AND status <> 'rejeitado'""",
                            [uid, inicio_semana])
    pendentes = db().val("SELECT count(*) FROM apontamentos WHERE status = 'pendente'") \
        if eh_admin() else None
    return render_template("equipe/painel.html", resumo=resumo, meus=meus, fila=fila,
                           minhas_horas=minhas_horas, pendentes=pendentes)


# =============================================================================
# Chamados
# =============================================================================
@bp.get("/chamados")
@requer("chamados")
def chamados():
    f = {k: request.args.get(k, "") for k in ("situacao", "cliente", "responsavel", "prioridade", "busca")}
    f["situacao"] = f["situacao"] or "abertos"
    where, params = [], []
    if f["situacao"] == "abertos":
        where.append("status IN ('novo','em_atendimento','aguardando_cliente')")
    elif f["situacao"] in ch.STATUS:
        where.append("status = %s"); params.append(f["situacao"])
    elif f["situacao"] in ("estourado", "em_risco"):
        where.append("situacao_sla = %s"); params.append(f["situacao"])
    if _int(f["cliente"]):
        where.append("cliente_id = %s"); params.append(int(f["cliente"]))
    if f["responsavel"] == "eu":
        where.append("responsavel_id = %s"); params.append(g.usuario["id"])
    elif f["responsavel"] == "ninguem":
        where.append("responsavel_id IS NULL")
    elif _int(f["responsavel"]):
        where.append("responsavel_id = %s"); params.append(int(f["responsavel"]))
    if f["prioridade"] in ch.PRIORIDADES:
        where.append("prioridade = %s"); params.append(f["prioridade"])
    if f["busca"].strip():
        termo = f["busca"].strip()
        if termo.isdigit():
            where.append("numero = %s"); params.append(int(termo))
        else:
            where.append("(titulo ILIKE %s OR descricao ILIKE %s)")
            params += [f"%{termo}%", f"%{termo}%"]
    fv, pv = ch.filtro_visivel()
    where.append(fv); params += pv
    sql = "SELECT * FROM vw_chamados_sla"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (status IN ('resolvido','fechado')), prazo_solucao NULLS LAST, numero DESC LIMIT 300"
    lista = db().all(sql, params)
    return render_template("equipe/chamados.html", lista=lista, f=f,
                           clientes=_clientes_ativos(), tecnicos=_tecnicos())


@bp.route("/chamados/novo", methods=["GET", "POST"])
@requer("chamados")
def chamado_novo():
    form = request.form
    if request.method == "POST":
        cliente_id = _int(form.get("cliente_id"))
        solicitante_id = _int(form.get("solicitante_id"))
        try:
            if not cliente_id:
                raise ValueError("Escolha o cliente.")
            if solicitante_id and not db().val(
                    "SELECT 1 FROM usuarios WHERE id = %s AND cliente_id = %s", [solicitante_id, cliente_id]):
                raise ValueError("O solicitante não é usuário desse cliente.")
            anexo = anx.validar(request.files.get("anexo"))
            novo = ch.criar(
                cliente_id=cliente_id, contrato_id=_int(form.get("contrato_id")),
                solicitante_id=solicitante_id or g.usuario["id"],
                titulo=form.get("titulo"), descricao=form.get("descricao"), categoria=form.get("categoria"),
                prioridade=form.get("prioridade", "media"), origem="interno",
                responsavel_id=_int(form.get("responsavel_id")))
            if anexo:
                anx.salvar(novo["id"], None, anexo, g.usuario["id"])
            notificar(novo["id"], "aberto")
            flash(f"Chamado #{novo['numero']} aberto.", "ok")
            return redirect(url_for("equipe.chamado", numero=novo["numero"]))
        except ValueError as e:
            flash(str(e), "erro")
    contratos = db().all("SELECT id, cliente_id, descricao, tipo FROM contratos WHERE ativo ORDER BY descricao")
    contatos = db().all("SELECT id, cliente_id, nome FROM usuarios WHERE perfil = 'cliente' AND ativo ORDER BY nome")
    return render_template("equipe/chamado_novo.html", form=form, clientes=_clientes_ativos(),
                           contratos=contratos, contatos=contatos, tecnicos=_tecnicos(),
                           prioridades=ch.PRIORIDADES)


@bp.get("/chamados/<int:numero>")
@requer("chamados")
def chamado(numero):
    c = ch.buscar_equipe(numero)
    tempo = ch.linha_do_tempo(c["id"])
    horas = db().all("""SELECT a.*, u.nome AS tecnico, ts.nome AS tipo_servico
                          FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                          LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
                         WHERE a.chamado_id = %s ORDER BY a.data DESC, a.id DESC""", [c["id"]])
    contratos = db().all("SELECT id, descricao, tipo FROM contratos WHERE cliente_id = %s AND ativo",
                         [c["cliente_id"]])
    solicitante = db().one("SELECT nome, email, codigo FROM usuarios WHERE id = %s", [c["solicitante_id"]]) \
        if c["solicitante_id"] else None
    anexos = anx.listar(c["id"])
    from .rotas_conhecimento import buscar_artigos, respostas_para
    from .rotas_gestao import estado_cronometro
    from .saldos import bloqueio_atendimento, saldo_chamado, situacao_contrato, tipos_permitidos
    cron = estado_cronometro(c["id"]) if pode("horas") else None
    saldo = saldo_chamado(c["id"])
    tipo_padrao = db().val("SELECT tipo_servico_id FROM contratos WHERE id = %s", [c["contrato_id"]]) \
        if c["contrato_id"] else None
    contratos = [{**ct, "situacao": situacao_contrato(ct["id"])} for ct in contratos]
    responsavel = db().val("SELECT nome FROM usuarios WHERE id = %s", [c["responsavel_id"]]) \
        if c["responsavel_id"] else None
    agendamentos = db().all("""SELECT a.*, u.nome AS recurso FROM agendamentos a JOIN usuarios u ON u.id = a.recurso_id
                                WHERE a.chamado_id = %s ORDER BY a.inicio DESC""", [c["id"]])
    return render_template("equipe/chamado.html", c=c, tempo=tempo, horas=horas, tecnicos=_tecnicos(),
                           contratos=contratos, solicitante=solicitante, prioridades=ch.PRIORIDADES, anexos=anexos,
                           agendamentos=agendamentos, cron=cron, saldo=saldo, tipo_padrao=tipo_padrao,
                           status_lista=ch.STATUS, hoje=_hoje(), tipos=tipos_permitidos(c["contrato_id"]),
                           bloqueio=bloqueio_atendimento(c), situacao=situacao_contrato(c["contrato_id"]),
                           responsavel=responsavel, gerir=pode("chamados_gerir"),
                           respostas=respostas_para(c, solicitante),
                           artigos=buscar_artigos(c["titulo"], so_publicos=False, limite=3),
                           extra_pendente=db().one("""SELECT * FROM horas_extras WHERE contrato_id = %s AND status = 'pendente'
                                                      ORDER BY id DESC LIMIT 1""", [c["contrato_id"]])
                           if c["contrato_id"] else None)


@bp.post("/chamados/<int:numero>/responder")
@requer("chamados")
def chamado_responder(numero):
    c = ch.buscar_equipe(numero)
    try:
        from .saldos import situacao_contrato
        s = situacao_contrato(c["contrato_id"])
        if not s["ok"] and not eh_admin():
            raise ValueError(s["motivo"] + " O chamado não pode ser alterado até ser vinculado a um contrato "
                             "ativo com saldo de horas.")
        interna = request.form.get("interna") == "1"
        texto = request.form.get("texto")
        anexo = anx.validar(request.files.get("anexo"))
        iid = ch.responder(c["id"], g.usuario["id"], texto, interna=interna)
        if anexo:
            anx.salvar(c["id"], iid, anexo, g.usuario["id"])
        novo_status = request.form.get("status_apos")
        if novo_status in ch.STATUS and novo_status != c["status"]:
            db().run("UPDATE chamados SET status = %s WHERE id = %s", [novo_status, c["id"]])
        if not interna:
            notificar(c["id"], "resolvido" if novo_status == "resolvido" else "resposta",
                      (texto or "").strip(), g.usuario["id"])
        if not c["responsavel_id"]:
            db().run("UPDATE chamados SET responsavel_id = %s WHERE id = %s", [g.usuario["id"], c["id"]])
        flash("Resposta registrada.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("equipe.chamado", numero=numero) + "#conversa")


@bp.post("/chamados/<int:numero>/atualizar")
@requer("chamados")
def chamado_atualizar(numero):
    c = ch.buscar_equipe(numero)
    f = request.form
    gerir = pode("chamados_gerir")  # só quem tem a rotina troca o CONTRATO; status/prioridade/responsável: toda a equipe
    status = f.get("status") if f.get("status") in ch.STATUS else c["status"]
    prioridade = f.get("prioridade") if f.get("prioridade") in ch.PRIORIDADES else c["prioridade"]
    resp = _int(f.get("responsavel_id"))
    if resp and not db().val("SELECT 1 FROM usuarios WHERE id = %s AND perfil <> 'cliente'", [resp]):
        abort(400)
    contrato = _int(f.get("contrato_id")) if gerir else c["contrato_id"]
    if contrato and not db().val("SELECT 1 FROM contratos WHERE id = %s AND cliente_id = %s",
                                 [contrato, c["cliente_id"]]):
        abort(400)
    from .saldos import situacao_contrato
    erro = None
    if not contrato or not resp:
        erro = "Contrato e responsável são obrigatórios para atender o chamado."
    else:
        sc = situacao_contrato(contrato)
        if not sc["ok"]:
            erro = sc["motivo"] + " Escolha um contrato ativo com saldo de horas."
    if erro and not eh_admin():
        flash(erro, "erro")
        return redirect(url_for("equipe.chamado", numero=numero))
    if erro:
        flash("Atenção (salvo como administrador): " + erro, "erro")
    db().run("""UPDATE chamados SET status = %s, prioridade = %s, responsavel_id = %s, contrato_id = %s,
                       categoria = %s WHERE id = %s""",
             [status, prioridade, resp, contrato, (f.get("categoria") or "").strip() or None, c["id"]])
    if status == "resolvido" and c["status"] != "resolvido":
        notificar(c["id"], "resolvido")
    if prioridade == "critica" and c["prioridade"] != "critica" and status not in ("resolvido", "fechado"):
        from .integracoes import alerta_critico
        alerta_critico(c["id"])
    flash("Chamado atualizado.", "ok")
    return redirect(url_for("equipe.chamado", numero=numero))


def _validar_apontamento(form):
    """Valida o formulário de horas. Devolve dict ou lança ValueError."""
    minutos = parse_duracao(form.get("duracao"))
    descricao = (form.get("descricao") or "").strip()
    try:
        data = dt.date.fromisoformat(form.get("data") or "")
    except ValueError:
        data = None
    tipo = _int(form.get("tipo_servico_id"))
    if not minutos or minutos <= 0 or minutos > 24 * 60:
        raise ValueError("Informe a duração (ex.: 1:30, 45m ou 2h).")
    if not data or data > _hoje():
        raise ValueError("Informe uma data válida (não pode ser futura).")
    if not descricao:
        raise ValueError("Descreva o que foi feito.")
    if not tipo or not db().val("SELECT 1 FROM tipos_servico WHERE id = %s AND ativo", [tipo]):
        raise ValueError("Escolha o tipo de serviço.")
    from .saldos import checar_periodo
    checar_periodo(data)
    return {"minutos": minutos, "descricao": descricao, "data": data, "tipo": tipo,
            "faturavel": form.get("faturavel") == "1"}


def _tipos_servico():
    return db().all("SELECT id, nome FROM tipos_servico WHERE ativo ORDER BY ordem, nome")


@bp.post("/chamados/<int:numero>/horas")
@requer("horas")
def chamado_horas(numero):
    c = ch.buscar_equipe(numero)
    try:
        v = _validar_apontamento(request.form)
        from .saldos import bloqueio_atendimento, saldo_chamado, validar_tipo
        b = bloqueio_atendimento(c)
        if b:
            raise ValueError(b)
        validar_tipo(c["contrato_id"], v["tipo"])
        s = saldo_chamado(c["id"])
        if s["restante"] is not None and v["minutos"] > s["restante"] and g.usuario["perfil"] != "admin":
            r = max(s["restante"], 0)
            raise ValueError(f"Saldo insuficiente: restam {r // 60}h{r % 60:02d} ({s['descricao']}). "
                             "Peça ao administrador para alocar mais horas.")
        db().run("""INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, faturavel, descricao,
                                              tipo_servico_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                 [g.usuario["id"], c["id"], v["data"], v["minutos"], v["faturavel"], v["descricao"], v["tipo"]])
        from .alertas import verificar_alertas_saldo
        verificar_alertas_saldo(c["contrato_id"])
        flash("Horas lançadas.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("equipe.chamado", numero=numero) + "#horas")


@bp.post("/horas/lancar")
@requer("horas")
def horas_lancar():
    """Horas direto no contrato (projeto ou suporte), sem chamado."""
    contrato = _int(request.form.get("contrato_id"))
    try:
        if not contrato or not db().val("SELECT 1 FROM contratos WHERE id = %s AND ativo", [contrato]):
            raise ValueError("Escolha o cliente e o contrato.")
        v = _validar_apontamento(request.form)
        from .saldos import situacao_contrato, validar_tipo
        sc = situacao_contrato(contrato)
        if not sc["ok"]:
            raise ValueError(sc["motivo"])
        if sc["saldo_min"] is not None and v["minutos"] > sc["saldo_min"] and not eh_admin():
            raise ValueError(f"Saldo insuficiente no contrato: restam {sc['saldo_min'] // 60}h{sc['saldo_min'] % 60:02d}.")
        validar_tipo(contrato, v["tipo"])
        db().run("""INSERT INTO apontamentos (usuario_id, contrato_id, data, minutos, faturavel, descricao,
                                              tipo_servico_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                 [g.usuario["id"], contrato, v["data"], v["minutos"], v["faturavel"], v["descricao"], v["tipo"]])
        from .alertas import verificar_alertas_saldo
        verificar_alertas_saldo(contrato)
        flash("Horas lançadas no contrato.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("equipe.horas", semana=request.form.get("data") or None))


# =============================================================================
# Clientes, contatos e contratos
# =============================================================================
@bp.get("/clientes")
@equipe
def clientes():
    lista = db().all("""
        SELECT c.*, (SELECT count(*) FROM contratos ct WHERE ct.cliente_id = c.id AND ct.ativo) AS contratos,
               (SELECT count(*) FROM chamados x WHERE x.cliente_id = c.id
                  AND x.status IN ('novo','em_atendimento','aguardando_cliente')) AS abertos
          FROM clientes c ORDER BY c.ativo DESC, coalesce(c.nome_fantasia, c.razao_social)""")
    return render_template("equipe/clientes.html", lista=lista)


CAMPOS_CLIENTE = ["razao_social", "nome_fantasia", "cnpj", "email_financeiro", "telefone", "endereco",
                  "dominio_email", "observacoes", "inscricao_estadual", "inscricao_municipal", "segmento", "site",
                  "cep", "cidade", "uf", "responsavel_id", "cliente_desde"]
UFS = ("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO").split()


def _dados_cliente(form):
    def txt(k):
        return (form.get(k) or "").strip() or None
    cnpj = so_digitos(form.get("cnpj"))
    if cnpj and not cnpj_valido(cnpj):
        raise ValueError("CNPJ inválido.")
    razao = txt("razao_social")
    if not razao:
        raise ValueError("Informe a razão social.")
    cep = so_digitos(form.get("cep"))
    if cep and len(cep) != 8:
        raise ValueError("CEP deve ter 8 dígitos.")
    uf = (txt("uf") or "").upper() or None
    if uf and uf not in UFS:
        raise ValueError("UF inválida.")
    resp = _int(form.get("responsavel_id"))
    if resp and not db().val("SELECT 1 FROM usuarios WHERE id = %s AND perfil <> 'cliente'", [resp]):
        raise ValueError("Responsável inválido.")
    desde = form.get("cliente_desde")
    try:
        desde = dt.date.fromisoformat(desde) if desde else None
    except ValueError:
        raise ValueError("Data 'Cliente desde' inválida.")
    v = {"razao_social": razao, "nome_fantasia": txt("nome_fantasia") or razao, "cnpj": cnpj,
         "email_financeiro": txt("email_financeiro"), "telefone": txt("telefone"), "endereco": txt("endereco"),
         "dominio_email": normalizar_dominio(form.get("dominio_email")), "observacoes": txt("observacoes"),
         "inscricao_estadual": txt("inscricao_estadual"), "inscricao_municipal": txt("inscricao_municipal"),
         "segmento": txt("segmento"), "site": txt("site"), "cep": cep, "cidade": txt("cidade"), "uf": uf,
         "responsavel_id": resp, "cliente_desde": desde}
    return [v[k] for k in CAMPOS_CLIENTE]


@bp.route("/clientes/novo", methods=["GET", "POST"])
@requer("clientes")
def cliente_novo():
    if request.method == "POST":
        try:
            cid = db().val(f"""INSERT INTO clientes ({', '.join(CAMPOS_CLIENTE)})
                               VALUES ({', '.join(['%s'] * len(CAMPOS_CLIENTE))}) RETURNING id""",
                           _dados_cliente(request.form))
            flash("Cliente cadastrado. Agora inclua o contrato e os contatos.", "ok")
            return redirect(url_for("equipe.cliente", cliente_id=cid))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Já existe cliente com esse CNPJ ou domínio de e-mail.", "erro")
    return render_template("equipe/cliente_form.html", c=request.form, novo=True, tecnicos=_tecnicos(), ufs=UFS)


@bp.route("/clientes/<int:cliente_id>", methods=["GET", "POST"])
@equipe
def cliente(cliente_id):
    c = db().one("SELECT * FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    if request.method == "POST":
        if not pode("clientes"):
            abort(403)
        try:
            sets = ", ".join(f"{k} = %s" for k in CAMPOS_CLIENTE)
            db().run(f"UPDATE clientes SET {sets}, ativo = %s WHERE id = %s",
                     _dados_cliente(request.form) + [request.form.get("ativo") == "1", cliente_id])
            flash("Cliente atualizado.", "ok")
            return redirect(url_for("equipe.cliente", cliente_id=cliente_id))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Já existe cliente com esse CNPJ ou domínio de e-mail.", "erro")
    contratos = db().all("SELECT * FROM contratos WHERE cliente_id = %s ORDER BY ativo DESC, data_inicio DESC",
                         [cliente_id])
    saldos = {s["contrato_id"]: s for s in db().all(
        "SELECT * FROM vw_saldo_banco_horas WHERE cliente_id = %s", [cliente_id])}
    contatos = db().all("SELECT * FROM usuarios WHERE cliente_id = %s ORDER BY ativo DESC, nome", [cliente_id])
    horas_mes = db().all("""SELECT h.*, ct.descricao FROM vw_horas_mes h JOIN contratos ct ON ct.id = h.contrato_id
                             WHERE h.cliente_id = %s ORDER BY mes DESC LIMIT 12""", [cliente_id])
    responsavel = db().val("SELECT nome FROM usuarios WHERE id = %s", [c["responsavel_id"]]) \
        if c.get("responsavel_id") else None
    return render_template("equipe/cliente.html", c=c, contratos=contratos, saldos=saldos,
                           contatos=contatos, horas_mes=horas_mes, tecnicos=_tecnicos(), ufs=UFS,
                           responsavel=responsavel)


def _voltar(padrao_):
    v = request.values.get("voltar") or ""
    return v if v.startswith("/") and not v.startswith("//") else padrao_


CAMPOS_CONTRATO = ["tipo", "descricao", "valor_mensal", "franquia_horas", "valor_hora", "horas_pacote",
                   "valor_total", "data_inicio", "data_fim", "dia_vencimento", "indice_reajuste", "mes_reajuste",
                   "sla_politica_id", "observacoes", "tipo_servico_id"]


def _itens_contrato(form):
    """Itens (tipos de serviço) do contrato vindos do formulário: listas item_tipo/item_horas/item_valor/item_desc."""
    tipos, horas = form.getlist("item_tipo"), form.getlist("item_horas")
    valores, descs = form.getlist("item_valor"), form.getlist("item_desc")
    itens, vistos = [], set()
    for i, t in enumerate(tipos):
        tid = _int(t)
        if not tid:
            continue
        if tid in vistos:
            raise ValueError("Cada tipo de serviço só pode aparecer uma vez nos itens do contrato.")
        if not db().val("SELECT 1 FROM tipos_servico WHERE id = %s", [tid]):
            raise ValueError("Tipo de serviço inválido nos itens.")
        vistos.add(tid)
        h = parse_decimal(horas[i] if i < len(horas) else None)
        v = parse_decimal(valores[i] if i < len(valores) else None)
        if (h is not None and h < 0) or (v is not None and v < 0):
            raise ValueError("Horas e valores dos itens não podem ser negativos.")
        itens.append({"tipo_servico_id": tid, "horas": h, "valor_hora": v, "ordem": i,
                      "descricao": ((descs[i] if i < len(descs) else "") or "").strip() or None})
    return itens


def _itens_form(form):
    """Reapresenta os itens digitados quando o formulário volta com erro."""
    tipos, horas = form.getlist("item_tipo"), form.getlist("item_horas")
    valores, descs = form.getlist("item_valor"), form.getlist("item_desc")
    return [{"tipo_servico_id": _int(t), "horas": horas[i] if i < len(horas) else "",
             "valor_hora": valores[i] if i < len(valores) else "", "descricao": descs[i] if i < len(descs) else ""}
            for i, t in enumerate(tipos) if t]


def salvar_itens(contrato_id, itens):
    db().run("DELETE FROM contrato_itens WHERE contrato_id = %s", [contrato_id])
    for i in itens:
        db().run("""INSERT INTO contrato_itens (contrato_id, tipo_servico_id, horas, valor_hora, descricao, ordem)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                 [contrato_id, i["tipo_servico_id"], i["horas"], i["valor_hora"], i["descricao"], i["ordem"]])


def itens_do_contrato(contrato_id):
    return db().all("""SELECT i.*, ts.nome FROM contrato_itens i JOIN tipos_servico ts ON ts.id = i.tipo_servico_id
                        WHERE i.contrato_id = %s ORDER BY i.ordem, i.id""", [contrato_id]) if contrato_id else []


def _dados_contrato(form):
    tipo = form.get("tipo")
    if tipo not in ("mensal_fixo", "banco_horas", "hora_avulsa", "projeto", "proposta_avulsa"):
        raise ValueError("Escolha o tipo do contrato.")
    desc = (form.get("descricao") or "").strip()
    if not desc:
        raise ValueError("Informe a descrição do contrato.")
    def data(k):
        v = form.get(k)
        return dt.date.fromisoformat(v) if v else None
    inicio = data("data_inicio")
    if not inicio:
        raise ValueError("Informe a data de início.")
    vals = {
        "tipo": tipo, "descricao": desc,
        "valor_mensal": parse_decimal(form.get("valor_mensal")),
        "franquia_horas": parse_decimal(form.get("franquia_horas")),
        "valor_hora": parse_decimal(form.get("valor_hora")),
        "horas_pacote": parse_decimal(form.get("horas_pacote")),
        "valor_total": parse_decimal(form.get("valor_total")),
        "data_inicio": inicio, "data_fim": data("data_fim"),
        "dia_vencimento": _int(form.get("dia_vencimento")),
        "indice_reajuste": (form.get("indice_reajuste") or "").strip() or None,
        "mes_reajuste": _int(form.get("mes_reajuste")),
        "sla_politica_id": _int(form.get("sla_politica_id")),
        "observacoes": (form.get("observacoes") or "").strip() or None,
        "tipo_servico_id": _int(form.get("tipo_servico_id")),
    }
    if vals["data_fim"] and vals["data_fim"] < inicio:
        raise ValueError("A data de fim não pode ser anterior à data de início.")
    if vals["dia_vencimento"] is not None and not 1 <= vals["dia_vencimento"] <= 31:
        raise ValueError("Dia de vencimento deve ser de 1 a 31.")
    if vals["mes_reajuste"] is not None and not 1 <= vals["mes_reajuste"] <= 12:
        raise ValueError("Mês do reajuste deve ser de 1 a 12.")
    for k in ("valor_mensal", "franquia_horas", "valor_hora", "horas_pacote", "valor_total"):
        if vals[k] is not None and vals[k] < 0:
            raise ValueError("Valores e horas não podem ser negativos.")
    itens = _itens_contrato(form)
    soma = sum((i["horas"] or 0) for i in itens)
    if itens and all(i["horas"] is not None for i in itens) and vals["horas_pacote"] is None:
        vals["horas_pacote"] = soma  # horas liberadas = soma dos itens
    if vals["horas_pacote"] is not None and soma > vals["horas_pacote"]:
        raise ValueError(f"A soma das horas dos itens ({soma}h) passa das horas liberadas do contrato "
                         f"({vals['horas_pacote']}h).")
    vals["itens"] = itens
    vals["tipo_servico_id"] = itens[0]["tipo_servico_id"] if itens else vals["tipo_servico_id"]
    if vals["sla_politica_id"] and not db().val("SELECT 1 FROM sla_politicas WHERE id = %s",
                                                [vals["sla_politica_id"]]):
        raise ValueError("Política de SLA inválida.")
    obrig = {"mensal_fixo": "valor_mensal", "banco_horas": "horas_pacote", "hora_avulsa": "valor_hora",
             "projeto": "valor_total", "proposta_avulsa": "valor_total"}[tipo]
    if vals[obrig] is None:
        nomes = {"valor_mensal": "o valor mensal", "horas_pacote": "as horas do pacote",
                 "valor_hora": "o valor da hora", "valor_total": "o valor total"}
        raise ValueError(f"Para este tipo de contrato, informe {nomes[obrig]}.")
    return vals


@bp.route("/clientes/<int:cliente_id>/contratos/novo", methods=["GET", "POST"])
@requer("contratos")
def contrato_novo(cliente_id):
    c = db().one("SELECT * FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    if request.method == "POST":
        try:
            v = _dados_contrato(request.form)
            novo_id = db().val(f"""INSERT INTO contratos (cliente_id, {', '.join(CAMPOS_CONTRATO)})
                                   VALUES (%s, {', '.join(['%s'] * len(CAMPOS_CONTRATO))}) RETURNING id""",
                               [cliente_id] + [v[k] for k in CAMPOS_CONTRATO])
            salvar_itens(novo_id, v["itens"])
            flash("Contrato incluído.", "ok")
            return redirect(_voltar(url_for("equipe.cliente", cliente_id=cliente_id)))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Dados do contrato inconsistentes. Confira os valores.", "erro")
    return render_template("equipe/contrato_form.html", cliente=c, ct=request.form, novo=True,
                           politicas=_politicas(), tipos=_tipos_servico(), voltar=_voltar(""),
                           itens=_itens_form(request.form))


@bp.route("/contratos/<int:contrato_id>", methods=["GET", "POST"])
@requer("contratos")
def contrato(contrato_id):
    ct = db().one("SELECT * FROM contratos WHERE id = %s", [contrato_id]) or abort(404)
    c = db().one("SELECT * FROM clientes WHERE id = %s", [ct["cliente_id"]])
    if request.method == "POST":
        try:
            v = _dados_contrato(request.form)
            sets = ", ".join(f"{k} = %s" for k in CAMPOS_CONTRATO)
            db().run(f"UPDATE contratos SET {sets}, ativo = %s WHERE id = %s",
                     [v[k] for k in CAMPOS_CONTRATO] + [request.form.get("ativo") == "1", contrato_id])
            salvar_itens(contrato_id, v["itens"])
            flash("Contrato atualizado.", "ok")
            return redirect(_voltar(url_for("equipe.cliente", cliente_id=c["id"])))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Dados do contrato inconsistentes. Confira os valores.", "erro")
        ct = dict(request.form)
        itens = _itens_form(request.form)
    else:
        itens = itens_do_contrato(contrato_id)
    return render_template("equipe/contrato_form.html", cliente=c, ct=ct, novo=False, contrato_id=contrato_id,
                           politicas=_politicas(), tipos=_tipos_servico(), voltar=_voltar(""), itens=itens)


@bp.post("/clientes/<int:cliente_id>/contatos")
@requer("clientes")
def contato_novo(cliente_id):
    cli = db().one("SELECT id, ativo FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    volta = request.form.get("voltar") or url_for("equipe.cliente", cliente_id=cliente_id)
    if not volta.startswith("/"):
        volta = url_for("equipe.cliente", cliente_id=cliente_id)
    if not cli["ativo"]:
        flash("Este cliente está inativo. Reative o cliente antes de cadastrar usuários.", "erro")
        return redirect(volta)
    nome = (request.form.get("nome") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    if not nome or "@" not in email:
        flash("Informe nome e e-mail do usuário.", "erro")
        return redirect(volta)
    senha = _senha_temporaria()
    try:
        codigo = db().val("""INSERT INTO usuarios (nome, email, perfil, cliente_id, telefone, cargo, recebe_nps,
                                                   senha_hash, senha_provisoria, recebe_alerta_saldo, aprova_horas_extras)
                             VALUES (%s,%s,'cliente',%s,%s,%s,%s,%s,true,%s,%s) RETURNING codigo""",
                          [nome, email, cliente_id, (request.form.get("telefone") or "").strip() or None,
                           (request.form.get("cargo") or "").strip() or None, request.form.get("recebe_nps") == "1",
                           generate_password_hash(senha), request.form.get("recebe_alerta_saldo") == "1",
                           request.form.get("aprova_horas_extras") == "1"])
        flash(f"Usuário {codigo} criado para {email}. Senha temporária: {senha} — envie ao usuário e peça "
              f"para trocar no primeiro acesso.", "ok")
    except IntegrityError:
        db().rollback()
        flash("Este e-mail já é usuário deste cliente.", "erro")
    return redirect(volta)


@bp.get("/usuarios-clientes")
@requer("clientes")
def usuarios_clientes():
    f = {k: request.args.get(k, "") for k in ("cliente", "busca", "situacao")}
    where, params = ["u.perfil = 'cliente'"], []
    if _int(f["cliente"]):
        where.append("u.cliente_id = %s"); params.append(int(f["cliente"]))
    if f["busca"].strip():
        where.append("(u.nome ILIKE %s OR u.email ILIKE %s OR u.codigo ILIKE %s)")
        params += [f"%{f['busca'].strip()}%"] * 3
    if f["situacao"] != "todos":
        where.append("u.ativo")
    lista = db().all(f"""SELECT u.*, coalesce(c.nome_fantasia, c.razao_social) AS cliente, c.ativo AS cliente_ativo
                          FROM usuarios u JOIN clientes c ON c.id = u.cliente_id
                         WHERE {' AND '.join(where)} ORDER BY cliente, u.nome""", params)
    return render_template("equipe/usuarios_clientes.html", lista=lista, f=f, clientes=_clientes_ativos())


# =============================================================================
# Usuários (equipe e contatos): ativar, perfil, nova senha
# =============================================================================
@bp.route("/equipe", methods=["GET", "POST"])
@admin
def equipe_lista():
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        perfil = request.form.get("perfil")
        if not nome or "@" not in email or perfil not in ("admin", "tecnico", "financeiro"):
            flash("Informe nome, e-mail e perfil.", "erro")
        elif perfil == "admin" and g.usuario["perfil"] != "admin":
            abort(403)
        else:
            try:
                senha = senha_temporaria()
                custo = parse_decimal(request.form.get("custo_hora"))
                novo_id = db().val("""INSERT INTO usuarios (nome, email, perfil, senha_hash, custo_hora, cargo,
                                                           senha_provisoria)
                                      VALUES (%s,%s,%s,%s,%s,%s,true) RETURNING id""",
                                   [nome, email, perfil, generate_password_hash(senha), custo,
                                    (request.form.get("cargo") or "").strip() or None])
                flash(f"{nome} incluído. Senha temporária: {senha} — informe à pessoa; no primeiro acesso o "
                      f"sistema pede uma senha nova. Confira abaixo as rotinas que ela pode acessar.", "ok")
                return redirect(url_for("equipe.usuario_editar", usuario_id=novo_id) + "#rotinas")
            except IntegrityError:
                db().rollback()
                flash("Já existe um recurso com esse e-mail.", "erro")
        return redirect(url_for("equipe.equipe_lista"))
    lista = db().all("SELECT * FROM usuarios WHERE perfil <> 'cliente' ORDER BY ativo DESC, nome")
    return render_template("equipe/equipe.html", lista=lista)


@bp.route("/equipe/acessos", methods=["GET", "POST"])
@admin
def acessos():
    from .acessos import ROTINAS, padrao
    usuarios = db().all("""SELECT id, nome, email, perfil, ativo FROM usuarios
                            WHERE perfil IN ('tecnico','financeiro') ORDER BY ativo DESC, nome""")
    if request.method == "POST":
        for u in usuarios:
            for r in ROTINAS:
                marcado = request.form.get(f"p_{u['id']}_{r}") == "1"
                if marcado == padrao(u["perfil"], r):
                    db().run("DELETE FROM permissoes WHERE usuario_id = %s AND rotina = %s", [u["id"], r])
                else:
                    db().run("""INSERT INTO permissoes (usuario_id, rotina, permitido) VALUES (%s,%s,%s)
                                ON CONFLICT (usuario_id, rotina) DO UPDATE SET permitido = EXCLUDED.permitido""",
                             [u["id"], r, marcado])
        flash("Acessos salvos.", "ok")
        return redirect(url_for("equipe.acessos"))
    matriz = {u["id"]: {r: pode(r, u) for r in ROTINAS} for u in usuarios}
    padroes = {u["id"]: {r: padrao(u["perfil"], r) for r in ROTINAS} for u in usuarios}
    return render_template("equipe/acessos.html", usuarios=usuarios, rotinas=ROTINAS, matriz=matriz,
                           padroes=padroes)


def _pode_mexer(u):
    if u["perfil"] == "cliente":
        return pode("clientes")
    if eh_master(u) and u["id"] != g.usuario["id"]:
        return False  # só o próprio master altera a conta master
    return eh_admin()


@bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@equipe
def usuario_editar(usuario_id):
    u = db().one("SELECT * FROM usuarios WHERE id = %s", [usuario_id]) or abort(404)
    if not _pode_mexer(u):
        abort(403)
    if request.method == "POST":
        f = request.form
        nome = (f.get("nome") or "").strip()
        email = (f.get("email") or "").strip().lower()
        try:
            if not nome or "@" not in email:
                raise ValueError("Informe nome e e-mail.")
            if eh_master(u) and email.lower() != (u["email"] or "").lower():
                raise ValueError("O e-mail do administrador master não pode ser alterado.")
            custo = parse_decimal(f.get("custo_hora")) if u["perfil"] != "cliente" else None
            if custo is not None and custo < 0:
                raise ValueError("Custo/hora inválido.")
            cli = u["perfil"] == "cliente"
            db().run("UPDATE usuarios SET recebe_alerta_saldo = %s, aprova_horas_extras = %s WHERE id = %s",
                     [cli and f.get("recebe_alerta_saldo") == "1", cli and f.get("aprova_horas_extras") == "1",
                      usuario_id])
            db().run("""UPDATE usuarios SET nome=%s, email=%s, telefone=%s, cargo=%s, departamento=%s, observacoes=%s,
                               custo_hora=%s, recebe_nps=%s WHERE id=%s""",
                     [nome, email, (f.get("telefone") or "").strip() or None, (f.get("cargo") or "").strip() or None,
                      (f.get("departamento") or "").strip() or None, (f.get("observacoes") or "").strip() or None,
                      custo, u["perfil"] == "cliente" and f.get("recebe_nps") == "1", usuario_id])
            if u["perfil"] != "cliente" and f.get("desativar_2fa") == "1" and g.usuario["perfil"] == "admin":
                db().run("UPDATE usuarios SET totp_ativo = false, totp_segredo = NULL WHERE id = %s", [usuario_id])
            if u["perfil"] != "cliente" and eh_admin():
                perfil = f.get("perfil") or u["perfil"]
                if perfil not in ("admin", "tecnico", "financeiro") or eh_master(u) or u["id"] == g.usuario["id"]:
                    perfil = u["perfil"]  # ninguém muda o próprio perfil; o master é sempre admin
                if perfil != u["perfil"]:
                    db().run("UPDATE usuarios SET perfil = %s WHERE id = %s", [perfil, usuario_id])
                if perfil != "admin":
                    salvar_permissoes({**u, "perfil": perfil}, set(f.getlist("rotinas")))
            flash("Cadastro atualizado.", "ok")
            if u["perfil"] == "cliente":
                return redirect(url_for("equipe.cliente", cliente_id=u["cliente_id"]))
            return redirect(url_for("equipe.equipe_lista"))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Já existe outro usuário com esse e-mail.", "erro")
        u = {**u, **{k: f.get(k) for k in ("nome", "email", "telefone", "cargo", "departamento", "observacoes",
                                            "custo_hora")}, "recebe_nps": f.get("recebe_nps") == "1",
             "recebe_alerta_saldo": f.get("recebe_alerta_saldo") == "1",
             "aprova_horas_extras": f.get("aprova_horas_extras") == "1"}
    cliente = db().one("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes WHERE id = %s",
                       [u["cliente_id"]]) if u.get("cliente_id") else None
    acessos = None
    if u["perfil"] != "cliente":
        acessos = [{"chave": r, "nome": d, "marcado": pode(r, u), "padrao": padrao(u["perfil"], r), "so_admin": False,
                    "perfis": " ".join(sorted(ps))} for r, (d, ps) in ROTINAS.items()]
        acessos += [{"chave": r, "nome": d, "marcado": u["perfil"] == "admin", "padrao": None, "so_admin": True, "perfis": ""}
                    for r, d in SO_ADMIN.items()]
    return render_template("equipe/usuario_form.html", u=u, cliente=cliente, acessos=acessos,
                           pode_perfil=eh_admin() and not eh_master(u) and u["id"] != g.usuario["id"])


@bp.post("/usuarios/<int:usuario_id>")
@equipe
def usuario_atualizar(usuario_id):
    u = db().one("SELECT * FROM usuarios WHERE id = %s", [usuario_id]) or abort(404)
    # recursos: rotina "equipe" (contas de administrador só por administrador); clientes: rotina "clientes"
    if not _pode_mexer(u):
        abort(403)
    acao = request.form.get("acao")
    if acao == "alternar":
        if u["id"] == g.usuario["id"] or eh_master(u):
            flash("Este usuário não pode ser desativado.", "erro")
        else:
            db().run("UPDATE usuarios SET ativo = NOT ativo WHERE id = %s", [usuario_id])
            flash(f"{u['nome']} {'desativado' if u['ativo'] else 'reativado'}.", "ok")
    elif acao == "senha":
        senha = _senha_temporaria()
        db().run("UPDATE usuarios SET senha_hash = %s, senha_provisoria = true WHERE id = %s",
                 [generate_password_hash(senha), usuario_id])
        flash(f"Nova senha temporária de {u['email']}: {senha} — informe ao usuário e peça para trocar.", "ok")
    elif acao == "link":
        from .auth import enviar_link_senha
        enviar_link_senha(u, forcar=True)
        flash(f"Link para criar nova senha enviado para {u['email']} (válido por 60 minutos).", "ok")
    elif acao == "perfil" and g.usuario["perfil"] == "admin" and u["perfil"] != "cliente":
        novo = request.form.get("perfil")
        if eh_master(u):
            flash("O administrador master não pode deixar de ser administrador.", "erro")
        elif novo in ("admin", "tecnico", "financeiro") and u["id"] != g.usuario["id"]:
            db().run("UPDATE usuarios SET perfil = %s WHERE id = %s", [novo, usuario_id])
            flash("Perfil alterado.", "ok")
    volta = request.form.get("voltar") or ""
    if volta.startswith("/") and not volta.startswith("//"):
        return redirect(volta)
    if u["perfil"] == "cliente":
        return redirect(url_for("equipe.cliente", cliente_id=u["cliente_id"]))
    return redirect(url_for("equipe.equipe_lista"))


# =============================================================================
# Horas: minhas horas e aprovação
# =============================================================================
@bp.get("/horas")
@equipe
def horas():
    if not (pode("horas") or eh_admin()):
        abort(403)
    a = request.args
    ref = a.get("semana")
    try:
        base = dt.date.fromisoformat(ref) if ref else _hoje()
    except ValueError:
        base = _hoje()
    inicio = base - dt.timedelta(days=base.weekday())
    fim = inicio + dt.timedelta(days=6)
    try:
        de = dt.date.fromisoformat(a["de"]) if a.get("de") else inicio
        ate = dt.date.fromisoformat(a["ate"]) if a.get("ate") else (fim if not a.get("de") else _hoje())
    except ValueError:
        de, ate = inicio, fim
    f = {"de": de, "ate": ate, "cliente": _int(a.get("cliente")), "tipo": _int(a.get("tipo")),
         "chamado": (a.get("chamado") or "").strip(), "descricao": (a.get("descricao") or "").strip(),
         "duracao": (a.get("duracao") or "").strip(), "status": a.get("status") or ""}
    where, p = ["a.usuario_id = %s", "a.data BETWEEN %s AND %s"], [g.usuario["id"], de, ate]
    if f["cliente"]:
        where.append("cl.id = %s"); p.append(f["cliente"])
    if f["tipo"]:
        where.append("a.tipo_servico_id = %s"); p.append(f["tipo"])
    if f["chamado"]:
        num = f["chamado"].lstrip("#")
        if num.isdigit():
            where.append("(c.numero = %s OR c.titulo ILIKE %s)"); p += [int(num), f"%{num}%"]
        else:
            where.append("(c.titulo ILIKE %s OR ct.descricao ILIKE %s)"); p += [f"%{f['chamado']}%"] * 2
    if f["descricao"]:
        where.append("a.descricao ILIKE %s"); p.append(f"%{f['descricao']}%")
    if f["duracao"]:
        m = parse_duracao(f["duracao"])
        if m:
            where.append("a.minutos = %s"); p.append(m)
    if f["status"] in ("pendente", "aprovado", "rejeitado"):
        where.append("a.status = %s"); p.append(f["status"])
    minhas = db().all(f"""
        SELECT a.*, c.numero, c.titulo, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
               ts.nome AS tipo_servico, ct.descricao AS contrato
          FROM apontamentos a
          LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
          LEFT JOIN chamados c ON c.id = a.chamado_id
          LEFT JOIN contratos ct ON ct.id = a.contrato_id
          LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
         WHERE {' AND '.join(where)}
         ORDER BY a.data, a.id""", p)
    formato = request.args.get("formato")
    if formato in ("xlsx", "pdf"):
        from . import exportar
        for l in minhas:
            l["chamado"] = f"#{l['numero']}" if l["numero"] else ""
            l["titulo_ref"] = l["titulo"] or l["contrato"]
        total = sum(l["minutos"] for l in minhas)
        dados = {"cards": [("Total", total, "horas"), ("Lançamentos", len(minhas), "num"),
                           ("Aprovadas", sum(l["minutos"] for l in minhas if l["status"] == "aprovado"), "horas"),
                           ("Pendentes", sum(l["minutos"] for l in minhas if l["status"] == "pendente"), "horas")],
                 "tabelas": [{"titulo": "Minhas horas", "linhas": minhas, "larguras": [8, 7, 24, 15, 11, 25, 7, 8],
                              "total": {"descricao": "Total", "minutos": total},
                              "colunas": [("data", "Data", "data"), ("chamado", "Chamado", "texto"),
                                          ("titulo_ref", "Título / contrato", "texto"), ("cliente", "Cliente", "texto"),
                                          ("tipo_servico", "Tipo", "texto"), ("descricao", "O que foi feito", "texto"),
                                          ("minutos", "Tempo", "horas"), ("status", "Status", "texto")]}]}
        return exportar.resposta(formato, f"Horas de {g.usuario['nome']}",
                                 f"Período: {de:%d/%m/%Y} a {ate:%d/%m/%Y}", dados, "minhas_horas")
    pendentes = db().val("SELECT count(*) FROM apontamentos WHERE status = 'pendente'") if eh_admin() else 0
    contratos = db().all("""SELECT ct.id, ct.cliente_id, ct.descricao, ct.tipo FROM contratos ct
                              JOIN clientes c ON c.id = ct.cliente_id WHERE ct.ativo AND c.ativo ORDER BY ct.descricao""")
    filtrado = any(a.get(k) for k in ("de", "ate", "cliente", "tipo", "chamado", "descricao", "duracao", "status"))
    return render_template("equipe/horas.html", minhas=minhas, pendentes=pendentes, inicio=inicio, fim=fim, f=f,
                           filtrado=filtrado, anterior=inicio - dt.timedelta(days=7),
                           proxima=inicio + dt.timedelta(days=7),
                           clientes=_clientes_ativos(), contratos=contratos, tipos=_tipos_servico(), hoje=_hoje())


@bp.post("/horas/aprovar")
@admin
def horas_aprovar():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    acao = request.form.get("acao")
    if not ids:
        flash("Selecione ao menos um lançamento.", "erro")
    elif acao in ("aprovado", "rejeitado"):
        from .rotas_gestao import notificar_aprovacao
        feitos = [i for i in ids if db().run("""UPDATE apontamentos SET status = %s, aprovado_por = %s
                                                 WHERE id = %s AND status = 'pendente'""", [acao, g.usuario["id"], i])]
        notificar_aprovacao(feitos, acao)
        flash(f"{len(feitos)} lançamento(s) {'aprovado(s)' if acao == 'aprovado' else 'rejeitado(s)'}.", "ok")
    return redirect(_voltar(url_for("gestao.aprovacao")))


@bp.post("/horas/<int:apontamento_id>/excluir")
@equipe
def hora_excluir(apontamento_id):
    a = db().one("SELECT * FROM apontamentos WHERE id = %s", [apontamento_id]) or abort(404)
    if a["usuario_id"] != g.usuario["id"] and not eh_admin():
        abort(403)
    from .saldos import periodo_fechado
    if a["status"] != "pendente":
        flash("Só é possível excluir lançamentos ainda não aprovados.", "erro")
    elif periodo_fechado(a["data"]):
        flash("Este lançamento está num período fechado e não pode ser excluído.", "erro")
    else:
        db().run("DELETE FROM apontamentos WHERE id = %s", [apontamento_id])
        flash("Lançamento excluído.", "ok")
    return redirect(request.referrer or url_for("equipe.horas"))


# =============================================================================
# Configuração: SLA
# =============================================================================
@bp.route("/config/sla", methods=["GET", "POST"])
@requer("config")
def config_sla():
    """Políticas de SLA: cada uma define prazos por prioridade; o contrato escolhe a política."""
    if request.method == "POST":
        acao = request.form.get("acao")
        pol_id = _int(request.form.get("politica_id"))
        nome = (request.form.get("nome") or "").strip()
        regras, erro = {}, None
        if acao in ("criar", "salvar"):
            for p in ch.PRIORIDADES:
                r = parse_duracao(request.form.get(f"{p}_resposta"))
                s = parse_duracao(request.form.get(f"{p}_solucao"))
                if not r or not s:
                    erro = f"Preencha os dois prazos da prioridade {p} (ex.: 2h, 30m, 1:30)."
                elif s < r:
                    erro = f"Prioridade {p}: o prazo de solução não pode ser menor que o de 1ª resposta."
                regras[p] = (r, s)
            if not nome:
                erro = "Dê um nome para a política (ex.: Contrato Premium)."
        if erro:
            flash(erro, "erro")
        elif acao == "criar":
            try:
                pid = db().val("INSERT INTO sla_politicas (nome) VALUES (%s) RETURNING id", [nome])
                for p, (r, s) in regras.items():
                    db().run("""INSERT INTO sla_regras (politica_id, prioridade, minutos_1a_resposta, minutos_solucao)
                                VALUES (%s,%s,%s,%s)""", [pid, p, r, s])
                flash(f"Política \"{nome}\" criada. Escolha-a no contrato do cliente.", "ok")
            except IntegrityError:
                db().rollback()
                flash("Já existe uma política com esse nome.", "erro")
        elif acao == "salvar" and pol_id:
            try:
                db().run("UPDATE sla_politicas SET nome = %s WHERE id = %s", [nome, pol_id])
                for p, (r, s) in regras.items():
                    db().run("""INSERT INTO sla_regras (politica_id, prioridade, minutos_1a_resposta, minutos_solucao)
                                VALUES (%s,%s,%s,%s)
                                ON CONFLICT (politica_id, prioridade) DO UPDATE
                                SET minutos_1a_resposta = EXCLUDED.minutos_1a_resposta,
                                    minutos_solucao = EXCLUDED.minutos_solucao""", [pol_id, p, r, s])
                flash("Prazos salvos. Valem para chamados abertos a partir de agora.", "ok")
            except IntegrityError:
                db().rollback()
                flash("Já existe uma política com esse nome.", "erro")
        elif acao == "padrao" and pol_id:
            db().run("UPDATE sla_politicas SET padrao = false WHERE padrao")
            db().run("UPDATE sla_politicas SET padrao = true WHERE id = %s", [pol_id])
            flash("Política padrão alterada (vale para contratos sem política definida).", "ok")
        elif acao == "excluir" and pol_id:
            em_uso = db().val("SELECT count(*) FROM contratos WHERE sla_politica_id = %s", [pol_id])
            if db().val("SELECT padrao FROM sla_politicas WHERE id = %s", [pol_id]):
                flash("A política padrão não pode ser excluída.", "erro")
            elif em_uso:
                flash(f"Política em uso por {em_uso} contrato(s). Troque a política desses contratos antes.", "erro")
            else:
                db().run("DELETE FROM sla_politicas WHERE id = %s", [pol_id])
                flash("Política excluída.", "ok")
        return redirect(url_for("equipe.config_sla"))
    politicas = db().all("""SELECT p.*, (SELECT count(*) FROM contratos c WHERE c.sla_politica_id = p.id) AS contratos
                              FROM sla_politicas p ORDER BY p.padrao DESC, p.nome""")
    regras = {}
    for r in db().all("SELECT * FROM sla_regras"):
        regras.setdefault(r["politica_id"], {})[r["prioridade"]] = r
    expediente = db().all("SELECT * FROM expediente ORDER BY dia_semana")
    feriados = db().all("SELECT * FROM feriados WHERE data >= current_date ORDER BY data LIMIT 12")
    return render_template("equipe/config_sla.html", politicas=politicas, regras=regras,
                           prioridades=ch.PRIORIDADES, expediente=expediente, feriados=feriados)


TIPOS_IMAGEM = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


@bp.route("/config/tipos-servico", methods=["GET", "POST"])
@requer("config")
def tipos_servico():
    if request.method == "POST":
        acao = request.form.get("acao")
        tid = _int(request.form.get("id"))
        nome = (request.form.get("nome") or "").strip()
        try:
            if acao == "criar":
                if not nome:
                    raise ValueError("Informe o nome do tipo de serviço.")
                ordem = db().val("SELECT coalesce(max(ordem), 0) + 1 FROM tipos_servico")
                db().run("INSERT INTO tipos_servico (nome, ordem) VALUES (%s, %s)", [nome, ordem])
                flash(f"Tipo \"{nome}\" criado.", "ok")
            elif acao == "salvar" and tid:
                if not nome:
                    raise ValueError("O nome não pode ficar vazio.")
                db().run("UPDATE tipos_servico SET nome = %s, ordem = %s WHERE id = %s",
                         [nome, _int(request.form.get("ordem")) or 0, tid])
                flash("Tipo de serviço atualizado.", "ok")
            elif acao == "alternar" and tid:
                db().run("UPDATE tipos_servico SET ativo = NOT ativo WHERE id = %s", [tid])
                flash("Situação alterada.", "ok")
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Já existe um tipo de serviço com esse nome.", "erro")
        return redirect(url_for("equipe.tipos_servico"))
    tipos = db().all("""SELECT t.*, (SELECT coalesce(sum(minutos),0) FROM apontamentos a
                                     WHERE a.tipo_servico_id = t.id) AS minutos
                          FROM tipos_servico t ORDER BY t.ativo DESC, t.ordem, t.nome""")
    return render_template("equipe/tipos_servico.html", tipos=tipos)


@bp.route("/config/integracoes", methods=["GET", "POST"])
@requer("config")
def config_integracoes():
    from .integracoes import chat, config, enviar_nps, salvar_config
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "chat":
            url = (request.form.get("chat_webhook") or "").strip()
            if url and not url.startswith("https://chat.googleapis.com/"):
                flash("O endereço deve começar com https://chat.googleapis.com/ (webhook do Google Chat).", "erro")
            else:
                salvar_config("chat_webhook", url or None)
                flash("Webhook do Google Chat salvo." if url else "Avisos no Google Chat desligados.", "ok")
        elif acao == "chat_teste":
            ok = chat(f"✅ Teste do SisPorto enviado por {g.usuario['nome']}. Os alertas de chamados chegarão aqui.")
            flash("Mensagem de teste enviada ao Google Chat." if ok else
                  "Não foi possível enviar. Confira o endereço do webhook.", "ok" if ok else "erro")
        elif acao == "nps":
            dia = _int(request.form.get("nps_dia")) or 1
            salvar_config("nps_ativo", "1" if request.form.get("nps_ativo") == "1" else "0")
            salvar_config("nps_dia", str(min(max(dia, 1), 28)))
            flash("Configuração do NPS salva.", "ok")
        elif acao == "nps_agora":
            n = enviar_nps(forcar=True)
            flash(f"{n} pesquisa(s) NPS colocadas na fila de envio deste mês.", "ok")
        return redirect(url_for("equipe.config_integracoes"))
    webhook = config("chat_webhook")
    dados = {
        "chat_configurado": bool(webhook),
        "chat_final": (webhook or "")[-6:],
        "nps_ativo": config("nps_ativo", "0") == "1",
        "nps_dia": config("nps_dia", "1"),
        "nps_usuarios": db().val("""SELECT count(*) FROM usuarios u JOIN clientes c ON c.id = u.cliente_id
                                     WHERE u.recebe_nps AND u.ativo AND c.ativo"""),
        "fila": db().val("SELECT count(*) FROM emails_saida WHERE enviado_em IS NULL"),
        "ultimo_envio": db().val("SELECT max(enviado_em) FROM emails_saida"),
        "drive_pendentes": db().val("SELECT count(*) FROM chamado_anexos WHERE drive_arquivo_id IS NULL AND arquivo IS NOT NULL"),
        "agenda_pendentes": db().val("SELECT count(*) FROM agendamentos WHERE status IN ('pendente','cancelar')"),
    }
    return render_template("equipe/config_integracoes.html", d=dados)


@bp.post("/chamados/<int:numero>/agendar")
@requer("chamados")
def chamado_agendar(numero):
    c = ch.buscar_equipe(numero)
    f = request.form
    try:
        tipo = f.get("tipo")
        if tipo not in ("visita", "reuniao"):
            raise ValueError("Escolha visita ou reunião.")
        inicio = dt.datetime.fromisoformat(f"{f.get('data')}T{f.get('hora')}").replace(tzinfo=TZ)
        minutos = parse_duracao(f.get("duracao") or "1h")
        if not minutos or minutos > 12 * 60:
            raise ValueError("Duração inválida (ex.: 1h, 1:30).")
        if inicio < dt.datetime.now(TZ) - dt.timedelta(hours=1):
            raise ValueError("A data/hora já passou.")
        recurso = _int(f.get("recurso_id")) or g.usuario["id"]
        rec = db().one("SELECT id, nome, email FROM usuarios WHERE id = %s AND perfil <> 'cliente' AND ativo", [recurso])
        if not rec:
            raise ValueError("Recurso inválido.")
        convidados = [rec["email"]]
        if f.get("convidar_cliente") == "1":
            sol = db().val("SELECT email FROM usuarios WHERE id = %s", [c["solicitante_id"]]) \
                if c["solicitante_id"] else c["solicitante_email"]
            if sol:
                convidados.append(sol)
        for extra in (f.get("convidados") or "").replace(";", ",").split(","):
            extra = extra.strip().lower()
            if extra:
                if "@" not in extra:
                    raise ValueError(f"E-mail de convidado inválido: {extra}")
                convidados.append(extra)
        local = (f.get("local") or "").strip() or None
        fim = inicio + dt.timedelta(minutes=minutos)
        db().run("""INSERT INTO agendamentos (chamado_id, recurso_id, tipo, inicio, fim, local, descricao, convidados,
                                              criado_por)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 [c["id"], rec["id"], tipo, inicio, fim, local, (f.get("descricao") or "").strip() or c["titulo"],
                  ",".join(dict.fromkeys(convidados)), g.usuario["id"]])
        quando = inicio.strftime("%d/%m/%Y às %H:%M")
        texto = (f"{'Visita técnica' if tipo == 'visita' else 'Reunião'} agendada para {quando} "
                 f"com {rec['nome']}" + (f" – {local}" if local else "") + ".")
        ch.responder(c["id"], g.usuario["id"], texto)
        notificar(c["id"], "resposta", texto, g.usuario["id"])
        flash("Agendado. O evento será criado no Google Agenda em até 5 minutos e os convites enviados.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("equipe.chamado", numero=numero) + "#agenda")


@bp.post("/agendamentos/<int:ag_id>/cancelar")
@requer("chamados")
def agendamento_cancelar(ag_id):
    a = db().one("""SELECT a.*, c.numero FROM agendamentos a JOIN chamados c ON c.id = a.chamado_id
                     WHERE a.id = %s""", [ag_id]) or abort(404)
    ch.buscar_equipe(a["numero"])
    novo = "cancelado" if a["status"] == "pendente" else "cancelar" if a["status"] == "criado" else a["status"]
    db().run("UPDATE agendamentos SET status = %s WHERE id = %s", [novo, ag_id])
    flash("Agendamento cancelado. O evento será removido da agenda.", "ok")
    return redirect(url_for("equipe.chamado", numero=a["numero"]) + "#agenda")


@bp.route("/config/marca", methods=["GET", "POST"])
@requer("config")
def config_marca():
    if request.method == "POST":
        if request.form.get("acao") == "restaurar":
            db().run("DELETE FROM configuracoes WHERE chave = 'logo'")
            flash("Logo padrão restaurado.", "ok")
        else:
            arq = request.files.get("logo")
            dados = arq.read() if arq else b""
            mime = next((m for sig, m in TIPOS_IMAGEM.items() if dados.startswith(sig)), None)
            if mime == "image/webp" and dados[8:12] != b"WEBP":
                mime = None
            if not dados:
                flash("Escolha o arquivo do logo.", "erro")
            elif not mime:
                flash("Formato não aceito. Envie PNG, JPG ou WEBP.", "erro")
            elif len(dados) > 1024 * 1024:
                flash("O arquivo deve ter no máximo 1 MB.", "erro")
            else:
                db().run("""INSERT INTO configuracoes (chave, arquivo, tipo_mime) VALUES ('logo', %s, %s)
                            ON CONFLICT (chave) DO UPDATE SET arquivo = EXCLUDED.arquivo,
                            tipo_mime = EXCLUDED.tipo_mime, atualizado_em = now()""", [dados, mime])
                flash("Logo atualizado.", "ok")
        return redirect(url_for("equipe.config_marca"))
    personalizado = db().one("SELECT atualizado_em FROM configuracoes WHERE chave = 'logo'")
    return render_template("equipe/config_marca.html", personalizado=personalizado)


@bp.route("/importar", methods=["GET", "POST"])
@requer("importar")
def importar():
    from .importacao import importar as executar
    resultado = None
    if request.method == "POST":
        arq = request.files.get("planilha")
        if not arq or not arq.filename.lower().endswith(".xlsx"):
            flash("Escolha a planilha .xlsx preenchida.", "erro")
        else:
            resumo, erros, acessos = executar(arq.read())
            if erros:
                db().rollback()
                resultado = {"erros": erros}
            else:
                resultado = {"resumo": resumo, "acessos": acessos}
    return render_template("equipe/importar.html", resultado=resultado)


@bp.get("/importar/modelo.xlsx")
@requer("importar")
def importar_modelo():
    from flask import Response
    from .importacao import gerar_modelo
    return Response(gerar_modelo(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=sisporto-importacao.xlsx"})


@bp.get("/emails-recusados")
@requer("emails")
def emails_recusados():
    lista = db().all("SELECT * FROM emails_recusados ORDER BY recebido_em DESC LIMIT 100")
    fila = db().val("SELECT count(*) FROM emails_saida WHERE enviado_em IS NULL")
    return render_template("equipe/emails_recusados.html", lista=lista, fila=fila)


# =============================================================================
# auxiliares
# =============================================================================
def _clientes_ativos():
    return db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes WHERE ativo ORDER BY 2")


def _politicas():
    return db().all("SELECT id, nome, padrao FROM sla_politicas ORDER BY padrao DESC, nome")


def _tecnicos():
    return db().all("SELECT id, nome FROM usuarios WHERE perfil <> 'cliente' AND ativo ORDER BY nome")


def _senha_temporaria():
    return senha_temporaria()
