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
    """Para a tela do chamado: segundos já contados, se está rodando e quanto ainda pode correr."""
    c = _cron(chamado_id)
    seg = sd.segundos_cronometro(c) if c else 0
    restante = sd.restante_para_cronometro(chamado_id, g.usuario["id"])
    return {"existe": bool(c), "segundos": seg, "rodando": bool(c and c["rodando_desde"]),
            "limite": restante, "esgotado": restante is not None and seg >= restante}


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
    ch_ = ch.buscar(numero)
    c = _cron(ch_["id"])
    destino = url_for("equipe.chamado", numero=numero) + "#cronometro"
    if acao == "iniciar":
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
            flash("O cronômetro parou: o saldo de horas deste chamado acabou.", "erro")
    elif acao == "finalizar" and c:
        if c["rodando_desde"]:
            _pausar(c, ch_["id"])
            c = _cron(ch_["id"])
        tipo = _int(request.form.get("tipo_servico_id"))
        descricao = (request.form.get("descricao") or "").strip()
        if c["segundos_acumulados"] < 1:
            flash("O cronômetro está zerado.", "erro")
        elif not descricao:
            flash("Descreva o que foi feito para registrar as horas.", "erro")
        elif not tipo or not db().val("SELECT 1 FROM tipos_servico WHERE id = %s AND ativo", [tipo]):
            flash("Escolha o tipo de serviço.", "erro")
        else:
            minutos = sd.minutos_de(c["segundos_acumulados"])
            db().run("""INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, faturavel, descricao,
                                                  tipo_servico_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                     [g.usuario["id"], ch_["id"], _hoje(), minutos, request.form.get("faturavel") == "1",
                      descricao, tipo])
            db().run("DELETE FROM cronometros WHERE id = %s", [c["id"]])
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
        status = f.get("status")
        if status not in ("pendente", "aprovado", "rejeitado"):
            raise ValueError("Status inválido.")
        v["status"] = status
    return v


@bp.get("/admin/apontamentos")
@admin
def apontamentos():
    f = {k: request.args.get(k, "") for k in ("recurso", "cliente", "status", "de", "ate")}
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
    linhas = db().all(f"""SELECT a.*, u.nome AS recurso, u.codigo, c.numero, ct.descricao AS contrato,
                                 coalesce(cl.nome_fantasia, cl.razao_social) AS cliente, ts.nome AS tipo
                            FROM apontamentos a JOIN usuarios u ON u.id = a.usuario_id
                            LEFT JOIN chamados c ON c.id = a.chamado_id
                            LEFT JOIN contratos ct ON ct.id = a.contrato_id
                            LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
                            LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
                           WHERE {' AND '.join(where)} ORDER BY a.data DESC, a.id DESC LIMIT 1000""", p)
    return render_template("gestao/apontamentos.html", linhas=linhas, f=f,
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
                tipo = _int(request.form.get("tipo_servico_id"))
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
        SELECT ct.id, ct.descricao, ct.tipo::text AS tipo, ct.horas_pacote, ct.tipo_servico_id, ts.nome AS tipo_servico,
               coalesce(cl.nome_fantasia, cl.razao_social) AS cliente,
               (SELECT coalesce(sum(horas_alocadas), 0) FROM chamados x WHERE x.contrato_id = ct.id) AS alocadas,
               (SELECT coalesce(sum(a.minutos), 0) FROM apontamentos a WHERE a.contrato_id = ct.id
                   AND a.status <> 'rejeitado' AND a.faturavel) AS consumido
          FROM contratos ct JOIN clientes cl ON cl.id = ct.cliente_id
          LEFT JOIN tipos_servico ts ON ts.id = ct.tipo_servico_id
         WHERE ct.ativo ORDER BY (ct.horas_pacote IS NULL), cliente, ct.descricao""")
    sel = _int(request.args.get("contrato"))
    chamados = []
    if sel:
        chamados = db().all("""SELECT c.numero, c.titulo, c.status::text AS status, c.horas_alocadas,
                                      (SELECT coalesce(sum(minutos),0) FROM apontamentos a
                                        WHERE a.chamado_id = c.id AND a.status <> 'rejeitado') AS usado
                                 FROM chamados c WHERE c.contrato_id = %s ORDER BY c.numero DESC""", [sel])
    return render_template("gestao/saldos.html", contratos=contratos, sel=sel, chamados=chamados, tipos=_tipos())
