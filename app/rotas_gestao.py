"""Cronômetro de atendimento, gestão de apontamentos (admin) e saldos de horas (admin)."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import chamados as ch
from . import saldos as sd
from .acessos import pode, requer
from .auth import admin
from .db import db
from .util import TZ, horas_dec, parse_decimal, parse_duracao

bp = Blueprint("gestao", __name__)


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _hoje():
    return dt.datetime.now(TZ).date()


def _tipos():
    return db().all("SELECT id, nome FROM tipos_servico WHERE ativo ORDER BY ordem, nome")


# =============================================================================
# Cronômetro no chamado
# =============================================================================
def _cron(chamado_id):
    return db().one("SELECT * FROM cronometros WHERE chamado_id = %s AND usuario_id = %s",
                    [chamado_id, g.usuario["id"]])


def estado_cronometro(chamado_id):
    """Para a tela do chamado: segundos já contados, se está rodando, quanto ainda pode correr e quantos
    outros cronômetros estão consumindo o mesmo saldo agora."""
    c = _cron(chamado_id)
    seg = sd.segundos_cronometro(c) if c else 0
    e = sd.estado_saldo_cronometro(chamado_id, g.usuario["id"])
    restante = e["limite"]
    return {"existe": bool(c), "segundos": seg, "rodando": bool(c and c["rodando_desde"]),
            "limite": restante, "outros_rodando": e["outros_rodando"],
            "esgotado": restante is not None and seg >= restante}


@bp.get("/chamados/<int:numero>/cronometro/estado")
@requer("horas")
def cronometro_estado(numero):
    """Consultado pela tela a cada 30 s para acompanhar os outros cronômetros do mesmo saldo."""
    from flask import jsonify
    return jsonify(estado_cronometro(ch.buscar_equipe(numero)["id"]))


def _pausar(c, chamado_id):
    """Acumula o tempo, respeitando o saldo. Devolve True se bateu no limite."""
    total = sd.segundos_cronometro(c)
    limite = sd.restante_para_cronometro(chamado_id, g.usuario["id"])
    bateu = limite is not None and total >= limite
    if bateu:
        total = limite
    db().run("UPDATE cronometros SET segundos_acumulados = %s, rodando_desde = NULL WHERE id = %s", [total, c["id"]])
    return bateu


@bp.post("/chamados/<int:numero>/cronometro/<acao>")
@requer("horas")
def cronometro(numero, acao):
    ch_ = ch.buscar_equipe(numero)
    c = _cron(ch_["id"])
    destino = url_for("equipe.chamado", numero=numero) + "#cronometro"
    if acao == "iniciar":
        bloqueio = sd.bloqueio_atendimento(ch_)
        if bloqueio:
            flash(bloqueio, "erro")
            return redirect(destino)
        limite = sd.restante_para_cronometro(ch_["id"], g.usuario["id"])
        ja = sd.segundos_cronometro(c) if c else 0
        if limite is not None and ja >= limite:
            flash("Saldo de horas esgotado para este chamado. Peça ao administrador para alocar mais horas.", "erro")
        elif c:
            if not c["rodando_desde"]:
                db().run("UPDATE cronometros SET rodando_desde = now() WHERE id = %s", [c["id"]])
        else:
            db().run("INSERT INTO cronometros (chamado_id, usuario_id, rodando_desde) VALUES (%s, %s, now())",
                     [ch_["id"], g.usuario["id"]])
    elif acao == "pausar" and c:
        if c["rodando_desde"] and _pausar(c, ch_["id"]):
            flash("O saldo de horas acabou e o cronômetro parou. Encerre a atuação e finalize o apontamento abaixo.",
                  "erro")
    elif acao == "finalizar" and c:
        if c["rodando_desde"]:
            _pausar(c, ch_["id"])
            c = _cron(ch_["id"])
        tipo = _int(request.form.get("tipo_servico_id"))
        descricao = (request.form.get("descricao") or "").strip()
        bloqueio = sd.bloqueio_atendimento(ch_, exigir_saldo=False)
        tipo_ok = tipo in {t["id"] for t in sd.tipos_permitidos(ch_["contrato_id"])}
        if bloqueio:
            flash(bloqueio, "erro")
        elif c["segundos_acumulados"] < 1:
            flash("O cronômetro está zerado.", "erro")
        elif not descricao:
            flash("Descreva o que foi feito para registrar as horas.", "erro")
        elif not tipo_ok:
            flash("Escolha um tipo de serviço previsto no contrato.", "erro")
        else:
            minutos = sd.minutos_de(c["segundos_acumulados"])
            db().run("""INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, faturavel, descricao,
                                                  tipo_servico_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                     [g.usuario["id"], ch_["id"], _hoje(), minutos, request.form.get("faturavel") == "1",
                      descricao, tipo])
            db().run("DELETE FROM cronometros WHERE id = %s", [c["id"]])
            from .alertas import verificar_alertas_saldo
            verificar_alertas_saldo(ch_["contrato_id"])
            flash(f"{minutos // 60}h{minutos % 60:02d} registradas no chamado.", "ok")
            destino = url_for("equipe.chamado", numero=numero) + "#horas"
    elif acao == "descartar" and c:
        db().run("DELETE FROM cronometros WHERE id = %s", [c["id"]])
        flash("Cronômetro descartado.", "ok")
    return redirect(destino)


# =============================================================================
# Apontamentos: admin edita de todos; demais só os próprios pendentes
# =============================================================================
def _form_apontamento(f, pode_tudo):
    minutos = parse_duracao(f.get("duracao"))
    try:
        data = dt.date.fromisoformat(f.get("data") or "")
    except ValueError:
        data = None
    tipo = _int(f.get("tipo_servico_id"))
    descricao = (f.get("descricao") or "").strip()
    if not minutos or minutos <= 0 or minutos > 24 * 60:
        raise ValueError("Informe a duração (ex.: 1:30, 45m ou 2h).")
    if not data or data > _hoje():
        raise ValueError("Informe uma data válida (não pode ser futura).")
    if not tipo:
        raise ValueError("Escolha o tipo de serviço.")
    if not descricao:
        raise ValueError("Descreva o que foi feito.")
    sd.checar_periodo(data)
    v = {"minutos": minutos, "data": data, "tipo_servico_id": tipo, "descricao": descricao,
         "faturavel": f.get("faturavel") == "1"}
    if pode_tudo:
        rec = _int(f.get("usuario_id"))
        if not rec or not db().val("SELECT 1 FROM usuarios WHERE id = %s AND perfil <> 'cliente'", [rec]):
            raise ValueError("Escolha o recurso.")
        v["usuario_id"] = rec
        numero = _int((f.get("chamado") or "").lstrip("#"))
        contrato = _int(f.get("contrato_id"))
        if numero:
            ch_ = db().one("SELECT id, contrato_id FROM chamados WHERE numero = %s", [numero])
            if not ch_:
                raise ValueError(f"Chamado #{numero} não existe.")
            v["chamado_id"], v["contrato_id"] = ch_["id"], ch_["contrato_id"] or contrato
        elif contrato:
            v["chamado_id"], v["contrato_id"] = None, contrato
        else:
            raise ValueError("Informe o chamado ou o contrato.")
        if v["contrato_id"]:
            sd.validar_tipo(v["contrato_id"], tipo)
        status = f.get("status")
        if status not in ("pendente", "aprovado", "rejeitado"):
            raise ValueError("Status inválido.")
        v["status"] = status
    return v


@bp.get("/admin/apontamentos")
@admin
def apontamentos():
    f = {k: request.args.get(k, "") for k in ("recurso", "cliente", "status", "de", "ate", "chamado", "contrato",
                                                "descricao")}
    hoje = _hoje()
    de = f["de"] or (hoje - dt.timedelta(days=30)).isoformat()
    ate = f["ate"] or hoje.isoformat()
    f["de"], f["ate"] = de, ate
    where, p = ["a.data BETWEEN %s AND %s"], [de, ate]
    if _int(f["recurso"]):
        where.append("a.usuario_id = %s"); p.append(int(f["recurso"]))
    if _int(f["cliente"]):
        where.append("cl.id = %s"); p.append(int(f["cliente"]))
    if f["status"] in ("pendente", "aprovado", "rejeitado"):
        where.append("a.status = %s"); p.append(f["status"])
    _filtro_texto(f, where, p)
    linhas = db().all(f"""SELECT a.*, u.nome AS recurso, u.codigo, c.numero, c.titulo, ct.descricao AS contrato,
                                 c.status::text AS status_chamado, sol.nome AS solicitante,
                                 coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, ts.nome AS tipo
                            FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                            LEFT JOIN chamados c ON c.id = a.chamado_id
                            LEFT JOIN usuarios sol ON sol.id = c.solicitante_id
                            LEFT JOIN contratos ct ON ct.id = a.contrato_id
                            LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
                            LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
                           WHERE {' AND '.join(where)} ORDER BY a.data DESC, a.id DESC LIMIT 5000""", p)
    if request.args.get("formato") in ("xlsx", "pdf"):
        return _exportar_apontamentos(request.args["formato"], "Apontamentos dos recursos", f, linhas)
    return render_template("gestao/apontamentos.html", linhas=linhas, f=f, contratos=_contratos_filtro(),
                           recursos=db().all("SELECT id, nome FROM usuarios WHERE perfil <> 'cliente' ORDER BY nome"),
                           clientes=db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes ORDER BY 2"))


@bp.route("/admin/apontamentos/novo", methods=["GET", "POST"])
@bp.route("/admin/apontamentos/<int:ap_id>", methods=["GET", "POST"])
@admin
def apontamento_admin(ap_id=None):
    a = db().one("""SELECT a.*, c.numero FROM apontamentos a LEFT JOIN chamados c ON c.id = a.chamado_id
                     WHERE a.id = %s""", [ap_id]) if ap_id else None
    if ap_id and not a:
        abort(404)
    if request.method == "POST":
        if a and sd.periodo_fechado(a["data"]):
            flash("Este lançamento está num período fechado. Reabra o período para alterá-lo.", "erro")
            return redirect(url_for("gestao.apontamentos"))
        if request.form.get("acao") == "excluir" and a:
            db().run("DELETE FROM apontamentos WHERE id = %s", [ap_id])
            flash("Apontamento excluído.", "ok")
            return redirect(url_for("gestao.apontamentos"))
        try:
            v = _form_apontamento(request.form, pode_tudo=True)
            campos = ["usuario_id", "chamado_id", "contrato_id", "data", "minutos", "faturavel", "descricao",
                      "tipo_servico_id", "status"]
            if a:
                aprov = (", aprovado_por = %s, aprovado_em = now()" if v["status"] != "pendente" else
                         ", aprovado_por = NULL, aprovado_em = NULL")
                params = [v[k] for k in campos] + ([g.usuario["id"]] if v["status"] != "pendente" else []) + [ap_id]
                db().run(f"UPDATE apontamentos SET {', '.join(k + ' = %s' for k in campos)}{aprov} WHERE id = %s", params)
                db().run("UPDATE apontamentos SET motivo_rejeicao = %s WHERE id = %s",
                         [(request.form.get("motivo") or "").strip() or None if v["status"] == "rejeitado" else None, ap_id])
                if a["status"] != v["status"] and v["status"] in ("aprovado", "rejeitado"):
                    notificar_aprovacao([ap_id], v["status"], request.form.get("motivo") or "")
                flash("Apontamento atualizado.", "ok")
            else:
                cols = campos + (["aprovado_por"] if v["status"] != "pendente" else [])
                vals = [v[k] for k in campos] + ([g.usuario["id"]] if v["status"] != "pendente" else [])
                db().run(f"INSERT INTO apontamentos ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
                flash("Apontamento gerado.", "ok")
            return redirect(url_for("gestao.apontamentos"))
        except ValueError as e:
            flash(str(e), "erro")
        a = {**(a or {}), **request.form.to_dict(), "faturavel": request.form.get("faturavel") == "1"}
    contratos = db().all("""SELECT ct.id, ct.descricao, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
                              FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id ORDER BY cliente, ct.descricao""")
    return render_template("gestao/apontamento_form.html", a=a or {"data": _hoje(), "status": "aprovado",
                                                                     "faturavel": True},
                           recursos=db().all("SELECT id, nome, codigo FROM usuarios WHERE perfil <> 'cliente' AND ativo ORDER BY nome"),
                           contratos=contratos, tipos=_tipos(), admin=True, hoje=_hoje())


@bp.route("/horas/<int:ap_id>/editar", methods=["GET", "POST"])
@requer("horas")
def apontamento_proprio(ap_id):
    """Recurso não-admin edita só os próprios lançamentos ainda pendentes."""
    a = db().one("""SELECT a.*, c.numero FROM apontamentos a LEFT JOIN chamados c ON c.id = a.chamado_id
                     WHERE a.id = %s""", [ap_id]) or abort(404)
    if g.usuario["perfil"] == "admin":
        return redirect(url_for("gestao.apontamento_admin", ap_id=ap_id))
    if a["usuario_id"] != g.usuario["id"]:
        abort(403)
    if a["status"] != "pendente":
        flash("Este lançamento já foi aprovado/rejeitado e só o administrador pode alterá-lo.", "erro")
        return redirect(url_for("equipe.horas"))
    if request.method == "POST":
        try:
            v = _form_apontamento(request.form, pode_tudo=False)
            if a["contrato_id"]:
                sd.validar_tipo(a["contrato_id"], v["tipo_servico_id"])
            db().run("""UPDATE apontamentos SET data=%s, minutos=%s, faturavel=%s, descricao=%s, tipo_servico_id=%s
                         WHERE id = %s AND usuario_id = %s AND status = 'pendente'""",
                     [v["data"], v["minutos"], v["faturavel"], v["descricao"], v["tipo_servico_id"], ap_id,
                      g.usuario["id"]])
            flash("Lançamento atualizado.", "ok")
            return redirect(url_for("equipe.horas", semana=v["data"].isoformat()))
        except ValueError as e:
            flash(str(e), "erro")
    return render_template("gestao/apontamento_form.html", a=a, tipos=_tipos(), admin=False, hoje=_hoje())


# =============================================================================
# Saldos de horas: projeto/contrato rateado entre chamados (admin)
# =============================================================================
@bp.route("/admin/saldos", methods=["GET", "POST"])
@admin
def saldos():
    if request.method == "POST":
        acao = request.form.get("acao")
        try:
            if acao == "alocar":
                numero = _int((request.form.get("chamado") or "").lstrip("#"))
                ch_ = db().one("SELECT id, contrato_id FROM chamados WHERE numero = %s", [numero]) if numero else None
                if not ch_:
                    raise ValueError("Chamado não encontrado.")
                txt = (request.form.get("horas") or "").strip()
                if txt == "":
                    db().run("UPDATE chamados SET horas_alocadas = NULL WHERE id = %s", [ch_["id"]])
                    flash(f"Alocação do chamado #{numero} removida (passa a usar o saldo geral do contrato).", "ok")
                else:
                    horas = parse_decimal(txt)
                    if horas is None or horas < 0:
                        raise ValueError("Horas inválidas.")
                    contrato = _int(request.form.get("contrato_id")) or ch_["contrato_id"]
                    if contrato != ch_["contrato_id"]:
                        db().run("UPDATE chamados SET contrato_id = %s WHERE id = %s", [contrato, ch_["id"]])
                    sd.validar_alocacao(contrato, ch_["id"], horas)
                    db().run("UPDATE chamados SET horas_alocadas = %s WHERE id = %s", [horas, ch_["id"]])
                    flash(f"{horas_dec(horas)} alocadas ao chamado #{numero}.", "ok")
            elif acao == "contrato":
                cid = _int(request.form.get("contrato_id"))
                horas = parse_decimal(request.form.get("horas_pacote"))
                tipo = _int(request.form.get("tipo_servico_id")) or db().val(
                    "SELECT tipo_servico_id FROM contratos WHERE id = %s", [cid])
                if horas is not None:
                    alocado = db().val("SELECT coalesce(sum(horas_alocadas),0) FROM chamados WHERE contrato_id = %s", [cid])
                    if horas < alocado:
                        raise ValueError(f"Já há {alocado}h alocadas aos chamados deste contrato.")
                db().run("UPDATE contratos SET horas_pacote = %s, tipo_servico_id = %s WHERE id = %s", [horas, tipo, cid])
                flash("Contrato atualizado.", "ok")
        except ValueError as e:
            db().rollback()
            flash(str(e), "erro")
        return redirect(url_for("gestao.saldos", contrato=request.form.get("contrato_id") or ""))
    contratos = db().all("""
        SELECT ct.id, ct.descricao, ct.tipo::text AS tipo, ct.horas_pacote, ct.tipo_servico_id,
               coalesce((SELECT string_agg(ts.nome, ', ' ORDER BY i.ordem) FROM contrato_itens i
                          JOIN tipos_servico ts ON ts.id = i.tipo_servico_id WHERE i.contrato_id = ct.id),
                        (SELECT nome FROM tipos_servico WHERE id = ct.tipo_servico_id)) AS tipo_servico,
               coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, s.periodo,
               (SELECT coalesce(sum(horas_alocadas), 0) FROM chamados x WHERE x.contrato_id = ct.id) AS alocadas,
               coalesce(round(s.horas_consumidas * 60)::int,
                        (SELECT coalesce(sum(a.minutos), 0) FROM apontamentos a WHERE a.contrato_id = ct.id
                            AND a.status <> 'rejeitado' AND a.faturavel)) AS consumido
          FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
          LEFT JOIN vw_saldo_banco_horas s ON s.contrato_id = ct.id
         WHERE ct.ativo ORDER BY (ct.horas_pacote IS NULL), cliente, ct.descricao""")
    for ct in contratos:
        ct["saldo"] = int(ct["horas_pacote"] * 60 - ct["consumido"]) if ct["horas_pacote"] is not None else None
    sel = _int(request.args.get("contrato"))
    chamados, itens = [], []
    if sel:
        chamados = db().all("""SELECT c.numero, c.titulo, c.status::text AS status, c.horas_alocadas,
                                      (SELECT coalesce(sum(minutos),0) FROM apontamentos a
                                        WHERE a.chamado_id = c.id AND a.status <> 'rejeitado') AS usado
                                 FROM chamados c WHERE c.contrato_id = %s ORDER BY c.numero DESC""", [sel])
        for c in chamados:
            c["restante"] = int(c["horas_alocadas"] * 60 - c["usado"]) if c["horas_alocadas"] is not None else None
        tipo_ct = next((c["tipo"] for c in contratos if c["id"] == sel), None)
        itens = db().all(f"""SELECT ts.nome, i.horas,
                                    (SELECT coalesce(sum(a.minutos), 0) FROM apontamentos a
                                      WHERE a.contrato_id = i.contrato_id AND a.tipo_servico_id = i.tipo_servico_id
                                        AND a.status <> 'rejeitado' AND a.faturavel {sd._filtro_mes(tipo_ct)}) AS consumido
                               FROM contrato_itens i JOIN tipos_servico ts ON ts.id = i.tipo_servico_id
                              WHERE i.contrato_id = %s ORDER BY i.ordem, i.id""", [sel])
        for i in itens:
            i["saldo"] = int(i["horas"] * 60 - i["consumido"]) if i["horas"] is not None else None
    if request.args.get("formato") in ("xlsx", "pdf"):
        from . import exportar
        tabs = [{"titulo": "Contratos", "linhas": contratos, "larguras": [18, 22, 10, 18, 8, 8, 8, 8],
                 "colunas": [("cliente", "Cliente", "texto"), ("descricao", "Contrato", "texto"),
                             ("tipo", "Modalidade", "texto"), ("tipo_servico", "Tipos de serviço", "texto"),
                             ("horas_pacote", "Liberadas", "horas_dec"), ("alocadas", "Alocadas", "horas_dec"),
                             ("consumido", "Consumido", "horas"), ("saldo", "Saldo", "horas")]}]
        if sel:
            nome = next((f"{c['cliente']} – {c['descricao']}" for c in contratos if c["id"] == sel), "")
            tabs += [{"titulo": f"Por tipo de serviço – {nome}", "linhas": itens,
                      "colunas": [("nome", "Tipo de serviço", "texto"), ("horas", "Liberadas", "horas_dec"),
                                  ("consumido", "Consumido", "horas"), ("saldo", "Saldo", "horas")]},
                     {"titulo": f"Rateio entre chamados – {nome}", "linhas": chamados, "larguras": [7, 40, 12, 9, 9, 9],
                      "colunas": [("numero", "Chamado", "texto"), ("titulo", "Título", "texto"),
                                  ("status", "Status", "texto"), ("horas_alocadas", "Alocadas", "horas_dec"),
                                  ("usado", "Apontado", "horas"), ("restante", "Restante", "horas")]}]
        return exportar.resposta(request.args["formato"], "Saldos de horas",
                                 "Mensal fixo: saldo do mês corrente · demais: saldo do contrato", {"tabelas": tabs},
                                 "saldos_de_horas")
    return render_template("gestao/saldos.html", contratos=contratos, sel=sel, chamados=chamados, tipos=_tipos(),
                           itens=itens)


# =============================================================================
# Aprovação de horas dos recursos (admin)
# =============================================================================
@bp.route("/admin/aprovacao", methods=["GET", "POST"])
@admin
def aprovacao():
    if request.method == "POST":
        ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
        acao = request.form.get("acao")
        motivo = (request.form.get("motivo") or "").strip()
        if not ids:
            flash("Selecione ao menos um lançamento.", "erro")
        elif acao == "rejeitado" and not motivo:
            flash("Informe o motivo da rejeição (o recurso verá o motivo).", "erro")
        elif acao in ("aprovado", "rejeitado"):
            feitos = []
            for i in ids:
                if db().run("""UPDATE apontamentos SET status = %s, aprovado_por = %s, aprovado_em = now(),
                                      motivo_rejeicao = %s
                                WHERE id = %s AND status = 'pendente'""",
                            [acao, g.usuario["id"], motivo if acao == "rejeitado" else None, i]):
                    feitos.append(i)
            notificar_aprovacao(feitos, acao, motivo)
            flash(f"{len(feitos)} lançamento(s) {'aprovado(s)' if acao == 'aprovado' else 'rejeitado(s)'}. "
                  "O recurso e você receberão o resumo por e-mail.", "ok")
        return redirect(request.full_path if request.query_string else url_for("gestao.aprovacao"))
    f = {k: request.args.get(k, "") for k in ("recurso", "cliente", "de", "ate", "solicitante", "status_chamado",
                                                "chamado", "contrato", "descricao")}
    where, p = ["a.status = 'pendente'"], []
    if _int(f["solicitante"]):
        where.append("c.solicitante_id = %s"); p.append(int(f["solicitante"]))
    if f["status_chamado"] in ch.STATUS:
        where.append("c.status::text = %s"); p.append(f["status_chamado"])
    _filtro_texto(f, where, p)
    if _int(f["recurso"]):
        where.append("a.usuario_id = %s"); p.append(int(f["recurso"]))
    if _int(f["cliente"]):
        where.append("cl.id = %s"); p.append(int(f["cliente"]))
    for k, op in (("de", ">="), ("ate", "<=")):
        try:
            if f[k]:
                where.append(f"a.data {op} %s"); p.append(dt.date.fromisoformat(f[k]))
        except ValueError:
            f[k] = ""
    linhas = db().all(f"""SELECT a.*, u.nome AS recurso, u.codigo, c.numero, c.titulo, ct.descricao AS contrato,
                                 coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, ts.nome AS tipo
                            FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                            LEFT JOIN chamados c ON c.id = a.chamado_id
                            LEFT JOIN contratos ct ON ct.id = a.contrato_id
                            LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
                            LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
                           WHERE {' AND '.join(where)} ORDER BY u.nome, a.data, a.id""", p)
    if request.args.get("formato") in ("xlsx", "pdf"):
        return _exportar_apontamentos(request.args["formato"], "Horas aguardando aprovação", f, linhas)
    por_recurso = {}
    for l in linhas:
        r = por_recurso.setdefault(l["recurso"], {"minutos": 0, "fat": 0, "n": 0})
        r["minutos"] += l["minutos"]; r["n"] += 1
        r["fat"] += l["minutos"] if l["faturavel"] else 0
    return render_template("gestao/aprovacao.html", linhas=linhas, f=f, por_recurso=por_recurso,
                           contratos=_contratos_filtro(), status_chamado=ch.STATUS,
                           solicitantes=db().all("""SELECT u.id, u.nome, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
                                                      FROM usuarios u JOIN clientes cl ON cl.id = u.cliente_id
                                                     WHERE u.perfil = 'cliente' ORDER BY cl.nome_fantasia, u.nome"""),
                           recursos=db().all("SELECT id, nome FROM usuarios WHERE perfil <> 'cliente' ORDER BY nome"),
                           clientes=db().all("SELECT id, coalesce(nome_fantasia, razao_social) AS nome FROM clientes ORDER BY 2"))


def notificar_aprovacao(ids, acao, motivo=""):
    """E-mail ao consultor (recurso) e ao aprovador com o detalhe dos lançamentos aprovados/rejeitados."""
    if not ids:
        return
    linhas = db().all(f"""SELECT a.id, a.data, a.minutos, a.descricao, a.faturavel, a.usuario_id, u.nome AS recurso,
                                u.email AS recurso_email, c.numero, c.titulo, ct.descricao AS contrato,
                                coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, ts.nome AS tipo
                           FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                           LEFT JOIN chamados c ON c.id = a.chamado_id
                           LEFT JOIN contratos ct ON ct.id = a.contrato_id
                           LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
                           LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
                          WHERE a.id IN ({', '.join(['%s'] * len(ids))}) ORDER BY u.nome, a.data, a.id""", list(ids))
    if not linhas:
        return
    verbo = "aprovados" if acao == "aprovado" else "rejeitados"
    aprovador = g.usuario
    quando = dt.datetime.now(TZ).strftime("%d/%m/%Y %H:%M")

    def bloco(itens):
        out = []
        for l in itens:
            ref = f"chamado #{l['numero']} – {l['titulo']}" if l["numero"] else f"contrato {l['contrato']}"
            out.append(f"• {l['data']:%d/%m/%Y} · {l['cliente'] or '—'} · {ref}\n"
                       f"  {l['tipo'] or 'Sem tipo'} · {horas_dec(l['minutos'] / 60)}"
                       f"{'' if l['faturavel'] else ' (não faturável)'} · {l['descricao']}")
        total = sum(l["minutos"] for l in itens)
        return "\n".join(out) + f"\n\nTotal: {horas_dec(total / 60)} em {len(itens)} lançamento(s)."

    rodape = (f"\n\nAção: {verbo.upper()} por {aprovador['nome']} em {quando}."
              + (f"\nMotivo: {motivo}" if motivo and acao == "rejeitado" else "")
              + f"\n\nVer no SisPorto: {url_for('equipe.horas', _external=True)}\n\n—\nSisPorto · Porto Soluções Corporativas")
    por_recurso = {}
    for l in linhas:
        por_recurso.setdefault((l["recurso"], l["recurso_email"]), []).append(l)
    for (nome, email), itens in por_recurso.items():
        corpo = (f"Olá, {nome.split()[0]}.\n\nSeus lançamentos de horas foram {verbo}:\n\n" + bloco(itens) + rodape)
        db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento) VALUES (%s, %s, %s, 'aprovacao_horas')""",
                 [email, f"SisPorto – horas {verbo} ({horas_dec(sum(i['minutos'] for i in itens) / 60)})", corpo])
    corpo = (f"Resumo da sua {'aprovação' if acao == 'aprovado' else 'rejeição'} de horas:\n\n"
             + "\n\n".join(f"{nome}:\n" + bloco(itens) for (nome, _e), itens in por_recurso.items()) + rodape)
    db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento) VALUES (%s, %s, %s, 'aprovacao_horas')""",
             [aprovador["email"], f"SisPorto – você {'aprovou' if acao == 'aprovado' else 'rejeitou'} "
                                  f"{len(linhas)} lançamento(s) de horas", corpo])


def _contratos_filtro():
    return db().all("""SELECT ct.id, ct.descricao, coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
                         FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id ORDER BY cliente, ct.descricao""")


def _filtro_texto(f, where, p):
    """Filtros comuns: chamado (nº ou parte do título), contrato e parte da descrição."""
    if f.get("chamado", "").strip():
        t = f["chamado"].strip().lstrip("#")
        if t.isdigit():
            where.append("(c.numero = %s OR c.titulo ILIKE %s)"); p += [int(t), f"%{t}%"]
        else:
            where.append("c.titulo ILIKE %s"); p.append(f"%{t}%")
    if _int(f.get("contrato")):
        where.append("a.contrato_id = %s"); p.append(int(f["contrato"]))
    if f.get("descricao", "").strip():
        where.append("a.descricao ILIKE %s"); p.append(f"%{f['descricao'].strip()}%")


def _exportar_apontamentos(formato, titulo, f, linhas):
    from . import exportar
    for l in linhas:
        l["chamado_txt"] = f"#{l['numero']}" if l.get("numero") else ""
        l["fat"] = "Sim" if l["faturavel"] else "Não"
    total = sum(l["minutos"] for l in linhas)
    por_rec = {}
    for l in linhas:
        r = por_rec.setdefault(l["recurso"], {"nome": l["recurso"], "minutos": 0, "n": 0})
        r["minutos"] += l["minutos"]; r["n"] += 1
    sub = " · ".join(x for x in [f"Período: {f['de']} a {f['ate']}" if f.get("de") else "",
                                 f"{len(linhas)} lançamento(s)"] if x)
    dados = {"cards": [("Total", total, "horas"), ("Lançamentos", len(linhas), "num"),
                       ("Faturáveis", sum(l["minutos"] for l in linhas if l["faturavel"]), "horas")],
             "tabelas": [{"titulo": "Por recurso", "linhas": list(por_rec.values()),
                          "colunas": [("nome", "Recurso", "texto"), ("n", "Lançamentos", "num"),
                                      ("minutos", "Horas", "horas")]},
                         {"titulo": "Lançamentos", "linhas": linhas, "larguras": [8, 11, 13, 6, 15, 13, 9, 20, 6, 4, 7],
                          "total": {"descricao": "Total", "minutos": total},
                          "colunas": [("data", "Data", "data"), ("recurso", "Recurso", "texto"),
                                      ("cliente", "Cliente", "texto"), ("chamado_txt", "Chamado", "texto"),
                                      ("titulo", "Título", "texto"), ("contrato", "Contrato", "texto"),
                                      ("tipo", "Tipo", "texto"), ("descricao", "O que foi feito", "texto"),
                                      ("minutos", "Tempo", "horas"), ("fat", "Fat.", "texto"),
                                      ("status", "Status", "texto")]}]}
    return exportar.resposta(formato, titulo, sub, dados, titulo.lower().replace(" ", "_"))


# =============================================================================
# Fechamento de período do timesheet (admin)
# =============================================================================
@bp.route("/admin/fechamento", methods=["GET", "POST"])
@admin
def fechamento():
    hoje = _hoje()
    fim_padrao = hoje.replace(day=1) - dt.timedelta(days=1)
    try:
        de = dt.date.fromisoformat(request.values.get("de") or fim_padrao.replace(day=1).isoformat())
        ate = dt.date.fromisoformat(request.values.get("ate") or fim_padrao.isoformat())
    except ValueError:
        de, ate = fim_padrao.replace(day=1), fim_padrao
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "reabrir":
            pid = _int(request.form.get("id"))
            p = db().one("SELECT * FROM periodos_fechados WHERE id = %s", [pid])
            if p:
                db().run("DELETE FROM periodos_fechados WHERE id = %s", [pid])
                flash(f"Período de {p['inicio']:%d/%m/%Y} a {p['fim']:%d/%m/%Y} reaberto.", "ok")
        elif acao == "fechar":
            pend = db().val("SELECT count(*) FROM apontamentos WHERE status = 'pendente' AND data BETWEEN %s AND %s",
                            [de, ate])
            sobrepoe = db().val("SELECT 1 FROM periodos_fechados WHERE inicio <= %s AND fim >= %s", [ate, de])
            cron = db().val("SELECT count(*) FROM cronometros")
            if ate < de:
                flash("A data final não pode ser anterior à inicial.", "erro")
            elif ate >= hoje:
                flash("Só é possível fechar períodos que já terminaram.", "erro")
            elif pend:
                flash(f"Há {pend} lançamento(s) pendente(s) no período. Aprove ou rejeite antes de fechar.", "erro")
            elif sobrepoe:
                flash("Este período se sobrepõe a outro já fechado.", "erro")
            else:
                db().run("""INSERT INTO periodos_fechados (inicio, fim, observacao, fechado_por)
                            VALUES (%s,%s,%s,%s)""", [de, ate, (request.form.get("observacao") or "").strip() or None,
                                                      g.usuario["id"]])
                flash(f"Período de {de:%d/%m/%Y} a {ate:%d/%m/%Y} fechado. As horas desse intervalo estão travadas."
                      + (f" Atenção: há {cron} cronômetro(s) aberto(s); ao finalizar, eles lançam com a data de hoje."
                         if cron else ""), "ok")
        return redirect(url_for("gestao.fechamento"))
    resumo = db().all("""SELECT u.nome AS recurso, count(*) AS n, sum(a.minutos) AS minutos,
                                count(*) FILTER (WHERE a.status = 'pendente') AS pendentes,
                                coalesce(sum(a.minutos) FILTER (WHERE a.status = 'aprovado'), 0) AS aprovados
                           FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                          WHERE a.data BETWEEN %s AND %s AND a.status <> 'rejeitado'
                          GROUP BY u.nome ORDER BY u.nome""", [de, ate])
    periodos = db().all("""SELECT p.*, u.nome AS quem,
                                  (SELECT coalesce(sum(minutos), 0) FROM apontamentos a
                                    WHERE a.data BETWEEN p.inicio AND p.fim AND a.status = 'aprovado') AS minutos
                             FROM periodos_fechados p LEFT JOIN usuarios u ON u.id = p.fechado_por
                            ORDER BY p.inicio DESC""")
    return render_template("gestao/fechamento.html", de=de, ate=ate, resumo=resumo, periodos=periodos, hoje=hoje)
