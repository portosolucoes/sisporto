"""Telas da equipe interna: painel, chamados, clientes, contratos, equipe e horas."""
from __future__ import annotations

import datetime as dt
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import chamados as ch
from .auth import admin, equipe, perfis
from .db import IntegrityError, db
from .util import TZ, cnpj_valido, parse_decimal, parse_duracao, so_digitos

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
    resumo = db().one("""
        SELECT count(*) FILTER (WHERE status IN ('novo','em_atendimento','aguardando_cliente')) AS abertos,
               count(*) FILTER (WHERE situacao_sla = 'estourado')  AS estourados,
               count(*) FILTER (WHERE situacao_sla = 'em_risco')   AS em_risco,
               count(*) FILTER (WHERE status = 'aguardando_cliente') AS aguardando,
               count(*) FILTER (WHERE responsavel_id IS NULL AND status IN ('novo','em_atendimento')) AS sem_responsavel
          FROM vw_chamados_sla""")
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
        if g.usuario["perfil"] == "admin" else None
    return render_template("equipe/painel.html", resumo=resumo, meus=meus, fila=fila,
                           minhas_horas=minhas_horas, pendentes=pendentes)


# =============================================================================
# Chamados
# =============================================================================
@bp.get("/chamados")
@equipe
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
    sql = "SELECT * FROM vw_chamados_sla"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (status IN ('resolvido','fechado')), prazo_solucao NULLS LAST, numero DESC LIMIT 300"
    lista = db().all(sql, params)
    return render_template("equipe/chamados.html", lista=lista, f=f,
                           clientes=_clientes_ativos(), tecnicos=_tecnicos())


@bp.route("/chamados/novo", methods=["GET", "POST"])
@equipe
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
            novo = ch.criar(
                cliente_id=cliente_id, contrato_id=_int(form.get("contrato_id")),
                solicitante_id=solicitante_id or g.usuario["id"],
                titulo=form.get("titulo"), descricao=form.get("descricao"), categoria=form.get("categoria"),
                prioridade=form.get("prioridade", "media"), origem="interno",
                responsavel_id=_int(form.get("responsavel_id")))
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
@equipe
def chamado(numero):
    c = ch.buscar(numero)
    tempo = ch.linha_do_tempo(c["id"])
    horas = db().all("""SELECT a.*, u.nome AS tecnico FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                         WHERE a.chamado_id = %s ORDER BY a.data DESC, a.id DESC""", [c["id"]])
    contratos = db().all("SELECT id, descricao, tipo FROM contratos WHERE cliente_id = %s AND ativo",
                         [c["cliente_id"]])
    solicitante = db().one("SELECT nome, email FROM usuarios WHERE id = %s", [c["solicitante_id"]]) \
        if c["solicitante_id"] else None
    return render_template("equipe/chamado.html", c=c, tempo=tempo, horas=horas, tecnicos=_tecnicos(),
                           contratos=contratos, solicitante=solicitante, prioridades=ch.PRIORIDADES,
                           status_lista=ch.STATUS, hoje=_hoje())


@bp.post("/chamados/<int:numero>/responder")
@equipe
def chamado_responder(numero):
    c = ch.buscar(numero)
    try:
        ch.responder(c["id"], g.usuario["id"], request.form.get("texto"),
                     interna=request.form.get("interna") == "1")
        novo_status = request.form.get("status_apos")
        if novo_status in ch.STATUS and novo_status != c["status"]:
            db().run("UPDATE chamados SET status = %s WHERE id = %s", [novo_status, c["id"]])
        if not c["responsavel_id"]:
            db().run("UPDATE chamados SET responsavel_id = %s WHERE id = %s", [g.usuario["id"], c["id"]])
        flash("Resposta registrada.", "ok")
    except ValueError as e:
        flash(str(e), "erro")
    return redirect(url_for("equipe.chamado", numero=numero) + "#conversa")


@bp.post("/chamados/<int:numero>/atualizar")
@equipe
def chamado_atualizar(numero):
    c = ch.buscar(numero)
    f = request.form
    status = f.get("status") if f.get("status") in ch.STATUS else c["status"]
    prioridade = f.get("prioridade") if f.get("prioridade") in ch.PRIORIDADES else c["prioridade"]
    resp = _int(f.get("responsavel_id"))
    if resp and not db().val("SELECT 1 FROM usuarios WHERE id = %s AND perfil <> 'cliente'", [resp]):
        abort(400)
    contrato = _int(f.get("contrato_id"))
    if contrato and not db().val("SELECT 1 FROM contratos WHERE id = %s AND cliente_id = %s",
                                 [contrato, c["cliente_id"]]):
        abort(400)
    db().run("""UPDATE chamados SET status = %s, prioridade = %s, responsavel_id = %s, contrato_id = %s,
                       categoria = %s WHERE id = %s""",
             [status, prioridade, resp, contrato, (f.get("categoria") or "").strip() or None, c["id"]])
    flash("Chamado atualizado.", "ok")
    return redirect(url_for("equipe.chamado", numero=numero))


@bp.post("/chamados/<int:numero>/horas")
@equipe
def chamado_horas(numero):
    c = ch.buscar(numero)
    minutos = parse_duracao(request.form.get("duracao"))
    descricao = (request.form.get("descricao") or "").strip()
    try:
        data = dt.date.fromisoformat(request.form.get("data") or "")
    except ValueError:
        data = None
    if not minutos or minutos <= 0 or minutos > 24 * 60:
        flash("Informe a duração (ex.: 1:30, 45m ou 2h).", "erro")
    elif not data or data > _hoje():
        flash("Informe uma data válida (não pode ser futura).", "erro")
    elif not descricao:
        flash("Descreva o que foi feito.", "erro")
    else:
        db().run("""INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, faturavel, descricao)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                 [g.usuario["id"], c["id"], data, minutos, request.form.get("faturavel") == "1", descricao])
        flash("Horas lançadas.", "ok")
    return redirect(url_for("equipe.chamado", numero=numero) + "#horas")


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


def _dados_cliente(form):
    cnpj = so_digitos(form.get("cnpj"))
    if cnpj and not cnpj_valido(cnpj):
        raise ValueError("CNPJ inválido.")
    razao = (form.get("razao_social") or "").strip()
    if not razao:
        raise ValueError("Informe a razão social.")
    dominio = (form.get("dominio_email") or "").strip().lower().lstrip("@") or None
    return [razao, (form.get("nome_fantasia") or "").strip() or razao, cnpj,
            (form.get("email_financeiro") or "").strip() or None, (form.get("telefone") or "").strip() or None,
            (form.get("endereco") or "").strip() or None, dominio, (form.get("observacoes") or "").strip() or None]


@bp.route("/clientes/novo", methods=["GET", "POST"])
@perfis("admin", "financeiro")
def cliente_novo():
    if request.method == "POST":
        try:
            cid = db().val("""INSERT INTO clientes (razao_social, nome_fantasia, cnpj, email_financeiro, telefone,
                                                    endereco, dominio_email, observacoes)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", _dados_cliente(request.form))
            flash("Cliente cadastrado. Agora inclua o contrato e os contatos.", "ok")
            return redirect(url_for("equipe.cliente", cliente_id=cid))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Já existe cliente com esse CNPJ ou domínio de e-mail.", "erro")
    return render_template("equipe/cliente_form.html", c=request.form, novo=True)


@bp.route("/clientes/<int:cliente_id>", methods=["GET", "POST"])
@equipe
def cliente(cliente_id):
    c = db().one("SELECT * FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    if request.method == "POST":
        if g.usuario["perfil"] not in ("admin", "financeiro"):
            abort(403)
        try:
            db().run("""UPDATE clientes SET razao_social=%s, nome_fantasia=%s, cnpj=%s, email_financeiro=%s,
                               telefone=%s, endereco=%s, dominio_email=%s, observacoes=%s, ativo=%s WHERE id=%s""",
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
    return render_template("equipe/cliente.html", c=c, contratos=contratos, saldos=saldos,
                           contatos=contatos, horas_mes=horas_mes)


CAMPOS_CONTRATO = ["tipo", "descricao", "valor_mensal", "franquia_horas", "valor_hora", "horas_pacote",
                   "valor_total", "data_inicio", "data_fim", "dia_vencimento", "indice_reajuste", "mes_reajuste"]


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
    }
    obrig = {"mensal_fixo": "valor_mensal", "banco_horas": "horas_pacote", "hora_avulsa": "valor_hora",
             "projeto": "valor_total", "proposta_avulsa": "valor_total"}[tipo]
    if vals[obrig] is None:
        nomes = {"valor_mensal": "o valor mensal", "horas_pacote": "as horas do pacote",
                 "valor_hora": "o valor da hora", "valor_total": "o valor total"}
        raise ValueError(f"Para este tipo de contrato, informe {nomes[obrig]}.")
    return vals


@bp.route("/clientes/<int:cliente_id>/contratos/novo", methods=["GET", "POST"])
@perfis("admin", "financeiro")
def contrato_novo(cliente_id):
    c = db().one("SELECT * FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    if request.method == "POST":
        try:
            v = _dados_contrato(request.form)
            db().run(f"""INSERT INTO contratos (cliente_id, {', '.join(CAMPOS_CONTRATO)})
                         VALUES (%s, {', '.join(['%s'] * len(CAMPOS_CONTRATO))})""",
                     [cliente_id] + [v[k] for k in CAMPOS_CONTRATO])
            flash("Contrato incluído.", "ok")
            return redirect(url_for("equipe.cliente", cliente_id=cliente_id))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Dados do contrato inconsistentes. Confira os valores.", "erro")
    return render_template("equipe/contrato_form.html", cliente=c, ct=request.form, novo=True)


@bp.route("/contratos/<int:contrato_id>", methods=["GET", "POST"])
@perfis("admin", "financeiro")
def contrato(contrato_id):
    ct = db().one("SELECT * FROM contratos WHERE id = %s", [contrato_id]) or abort(404)
    c = db().one("SELECT * FROM clientes WHERE id = %s", [ct["cliente_id"]])
    if request.method == "POST":
        try:
            v = _dados_contrato(request.form)
            sets = ", ".join(f"{k} = %s" for k in CAMPOS_CONTRATO)
            db().run(f"UPDATE contratos SET {sets}, ativo = %s WHERE id = %s",
                     [v[k] for k in CAMPOS_CONTRATO] + [request.form.get("ativo") == "1", contrato_id])
            flash("Contrato atualizado.", "ok")
            return redirect(url_for("equipe.cliente", cliente_id=c["id"]))
        except ValueError as e:
            flash(str(e), "erro")
        except IntegrityError:
            db().rollback()
            flash("Dados do contrato inconsistentes. Confira os valores.", "erro")
        ct = dict(request.form)
    return render_template("equipe/contrato_form.html", cliente=c, ct=ct, novo=False, contrato_id=contrato_id)


@bp.post("/clientes/<int:cliente_id>/contatos")
@perfis("admin", "financeiro", "tecnico")
def contato_novo(cliente_id):
    db().one("SELECT id FROM clientes WHERE id = %s", [cliente_id]) or abort(404)
    nome = (request.form.get("nome") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    if not nome or "@" not in email:
        flash("Informe nome e e-mail do contato.", "erro")
        return redirect(url_for("equipe.cliente", cliente_id=cliente_id))
    senha = _senha_temporaria()
    try:
        db().run("""INSERT INTO usuarios (nome, email, perfil, cliente_id, telefone, senha_hash)
                    VALUES (%s,%s,'cliente',%s,%s,%s)""",
                 [nome, email, cliente_id, (request.form.get("telefone") or "").strip() or None,
                  generate_password_hash(senha)])
        flash(f"Acesso criado para {email}. Senha temporária: {senha} — envie ao contato e peça para trocar.",
              "ok")
    except IntegrityError:
        db().rollback()
        flash("Já existe um usuário com esse e-mail.", "erro")
    return redirect(url_for("equipe.cliente", cliente_id=cliente_id))


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
        else:
            try:
                db().run("INSERT INTO usuarios (nome, email, perfil) VALUES (%s,%s,%s)", [nome, email, perfil])
                flash(f"{nome} incluído. Ele entra com a conta Google {email}.", "ok")
            except IntegrityError:
                db().rollback()
                flash("Já existe um usuário com esse e-mail.", "erro")
        return redirect(url_for("equipe.equipe_lista"))
    lista = db().all("SELECT * FROM usuarios WHERE perfil <> 'cliente' ORDER BY ativo DESC, nome")
    return render_template("equipe/equipe.html", lista=lista)


@bp.post("/usuarios/<int:usuario_id>")
@perfis("admin", "financeiro", "tecnico")
def usuario_atualizar(usuario_id):
    u = db().one("SELECT * FROM usuarios WHERE id = %s", [usuario_id]) or abort(404)
    # só admin mexe em usuários da equipe; contatos de cliente: qualquer membro da equipe
    if u["perfil"] != "cliente" and g.usuario["perfil"] != "admin":
        abort(403)
    acao = request.form.get("acao")
    if acao == "alternar":
        if u["id"] == g.usuario["id"]:
            flash("Você não pode desativar o próprio usuário.", "erro")
        else:
            db().run("UPDATE usuarios SET ativo = NOT ativo WHERE id = %s", [usuario_id])
            flash(f"{u['nome']} {'desativado' if u['ativo'] else 'reativado'}.", "ok")
    elif acao == "senha":
        senha = _senha_temporaria()
        db().run("UPDATE usuarios SET senha_hash = %s WHERE id = %s", [generate_password_hash(senha), usuario_id])
        flash(f"Nova senha temporária de {u['email']}: {senha}", "ok")
    elif acao == "perfil" and g.usuario["perfil"] == "admin" and u["perfil"] != "cliente":
        novo = request.form.get("perfil")
        if novo in ("admin", "tecnico", "financeiro") and u["id"] != g.usuario["id"]:
            db().run("UPDATE usuarios SET perfil = %s WHERE id = %s", [novo, usuario_id])
            flash("Perfil alterado.", "ok")
    if u["perfil"] == "cliente":
        return redirect(url_for("equipe.cliente", cliente_id=u["cliente_id"]))
    return redirect(url_for("equipe.equipe_lista"))


# =============================================================================
# Horas: minhas horas e aprovação
# =============================================================================
@bp.get("/horas")
@equipe
def horas():
    ref = request.args.get("semana")
    try:
        base = dt.date.fromisoformat(ref) if ref else _hoje()
    except ValueError:
        base = _hoje()
    inicio = base - dt.timedelta(days=base.weekday())
    fim = inicio + dt.timedelta(days=6)
    minhas = db().all("""
        SELECT a.*, c.numero, c.titulo, cl.nome_fantasia AS cliente
          FROM apontamentos a
          LEFT JOIN chamados c ON c.id = a.chamado_id
          LEFT JOIN contratos ct ON ct.id = a.contrato_id
          LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
         WHERE a.usuario_id = %s AND a.data BETWEEN %s AND %s
         ORDER BY a.data, a.id""", [g.usuario["id"], inicio, fim])
    pendentes = []
    if g.usuario["perfil"] == "admin":
        pendentes = db().all("""
            SELECT a.*, u.nome AS tecnico, c.numero, c.titulo, cl.nome_fantasia AS cliente
              FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
              LEFT JOIN chamados c ON c.id = a.chamado_id
              LEFT JOIN contratos ct ON ct.id = a.contrato_id
              LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
             WHERE a.status = 'pendente' ORDER BY a.data, u.nome""")
    return render_template("equipe/horas.html", minhas=minhas, pendentes=pendentes, inicio=inicio, fim=fim,
                           anterior=inicio - dt.timedelta(days=7), proxima=inicio + dt.timedelta(days=7))


@bp.post("/horas/aprovar")
@admin
def horas_aprovar():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    acao = request.form.get("acao")
    if not ids:
        flash("Selecione ao menos um lançamento.", "erro")
    elif acao in ("aprovado", "rejeitado"):
        n = 0
        for i in ids:
            n += db().run("""UPDATE apontamentos SET status = %s, aprovado_por = %s
                              WHERE id = %s AND status = 'pendente'""", [acao, g.usuario["id"], i])
        flash(f"{n} lançamento(s) {'aprovado(s)' if acao == 'aprovado' else 'rejeitado(s)'}.", "ok")
    return redirect(url_for("equipe.horas"))


@bp.post("/horas/<int:apontamento_id>/excluir")
@equipe
def hora_excluir(apontamento_id):
    a = db().one("SELECT * FROM apontamentos WHERE id = %s", [apontamento_id]) or abort(404)
    if a["usuario_id"] != g.usuario["id"] and g.usuario["perfil"] != "admin":
        abort(403)
    if a["status"] != "pendente":
        flash("Só é possível excluir lançamentos ainda não aprovados.", "erro")
    else:
        db().run("DELETE FROM apontamentos WHERE id = %s", [apontamento_id])
        flash("Lançamento excluído.", "ok")
    return redirect(request.referrer or url_for("equipe.horas"))


# =============================================================================
# Configuração: SLA
# =============================================================================
@bp.route("/config/sla", methods=["GET", "POST"])
@admin
def config_sla():
    pol = db().one("SELECT * FROM sla_politicas WHERE padrao")
    if request.method == "POST" and pol:
        for p in ch.PRIORIDADES:
            r = parse_duracao(request.form.get(f"{p}_resposta"))
            s = parse_duracao(request.form.get(f"{p}_solucao"))
            if r and s and s >= r:
                db().run("""UPDATE sla_regras SET minutos_1a_resposta = %s, minutos_solucao = %s
                             WHERE politica_id = %s AND prioridade = %s""", [r, s, pol["id"], p])
        flash("Prazos atualizados. Valem para chamados novos.", "ok")
        return redirect(url_for("equipe.config_sla"))
    regras = db().all("""SELECT * FROM sla_regras WHERE politica_id = %s
                          ORDER BY array_position(ARRAY['critica','alta','media','baixa']::prioridade[], prioridade)""",
                      [pol["id"]]) if pol else []
    expediente = db().all("SELECT * FROM expediente ORDER BY dia_semana")
    feriados = db().all("SELECT * FROM feriados WHERE data >= current_date ORDER BY data LIMIT 12")
    return render_template("equipe/config_sla.html", regras=regras, expediente=expediente, feriados=feriados)


# =============================================================================
# auxiliares
# =============================================================================
def _clientes_ativos():
    return db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes WHERE ativo ORDER BY 2")


def _tecnicos():
    return db().all("SELECT id, nome FROM usuarios WHERE perfil <> 'cliente' AND ativo ORDER BY nome")


def _senha_temporaria():
    return secrets.token_urlsafe(9)
