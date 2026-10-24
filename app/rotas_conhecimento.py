"""Respostas prontas (macros) e base de conhecimento (artigos internos e públicos, ajuda no portal)."""
from __future__ import annotations

import re

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .acessos import pode, requer
from .auth import login_obrigatorio, perfis
from .db import IntegrityError, db

bp = Blueprint("kb", __name__)
cliente = perfis("cliente")

PALAVRAS_VAZIAS = set("a o e de da do das dos em no na nos nas um uma para por com sem que nao não meu minha "
                      "esta está estou ao aos as os se ja já mais".split())


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def buscar_artigos(termo, so_publicos, limite=20):
    """Busca simples por palavras (título pesa mais que conteúdo)."""
    palavras = [p for p in re.findall(r"\w+", (termo or "").lower()) if len(p) > 2 and p not in PALAVRAS_VAZIAS][:6]
    filtro = "a.ativo" + (" AND a.publico" if so_publicos else "")
    if not palavras:
        return db().all(f"""SELECT a.id, a.titulo, a.categoria, a.publico, a.visualizacoes, left(a.conteudo, 220) AS resumo
                             FROM artigos a WHERE {filtro} ORDER BY a.visualizacoes DESC, a.titulo LIMIT %s""", [limite])
    pontos, p = [], []
    for w in palavras:
        pontos.append("(CASE WHEN a.titulo ILIKE %s THEN 3 ELSE 0 END + CASE WHEN coalesce(a.palavras,'') ILIKE %s "
                      "THEN 2 ELSE 0 END + CASE WHEN a.conteudo ILIKE %s THEN 1 ELSE 0 END)")
        p += [f"%{w}%"] * 3
    soma = " + ".join(pontos)
    return db().all(f"""SELECT * FROM (SELECT a.id, a.titulo, a.categoria, a.publico, a.visualizacoes,
                                             left(a.conteudo, 220) AS resumo, ({soma}) AS pontos
                                        FROM artigos a WHERE {filtro}) x
                        WHERE pontos > 0 ORDER BY pontos DESC, visualizacoes DESC LIMIT %s""", p + [limite])


# =============================================================================
# Respostas prontas
# =============================================================================
@bp.route("/config/respostas-prontas", methods=["GET", "POST"])
@requer("config")
def respostas():
    if request.method == "POST":
        f = request.form
        rid = _int(f.get("id"))
        if f.get("acao") == "excluir" and rid:
            db().run("DELETE FROM respostas_prontas WHERE id = %s", [rid])
            flash("Resposta excluída.", "ok")
        else:
            titulo, texto = (f.get("titulo") or "").strip(), (f.get("texto") or "").strip()
            if not titulo or not texto:
                flash("Informe título e texto.", "erro")
            else:
                try:
                    if rid:
                        db().run("UPDATE respostas_prontas SET titulo=%s, texto=%s, ativo=%s WHERE id=%s",
                                 [titulo, texto, f.get("ativo") == "1", rid])
                    else:
                        db().run("INSERT INTO respostas_prontas (titulo, texto) VALUES (%s,%s)", [titulo, texto])
                    flash("Resposta pronta salva.", "ok")
                except IntegrityError:
                    db().rollback()
                    flash("Já existe uma resposta com esse título.", "erro")
        return redirect(url_for("kb.respostas"))
    lista = db().all("SELECT * FROM respostas_prontas ORDER BY ativo DESC, titulo")
    return render_template("kb/respostas.html", lista=lista, editar=_int(request.args.get("editar")))


def respostas_para(c, solicitante=None):
    """Respostas ativas já com os campos do chamado preenchidos (para o seletor da tela do chamado)."""
    valores = {"cliente": c.get("cliente_nome") or "", "numero": str(c.get("numero") or ""),
               "titulo": c.get("titulo") or "", "recurso": g.usuario["nome"],
               "solicitante": (solicitante or {}).get("nome") or c.get("solicitante_email") or ""}
    out = []
    for r in db().all("SELECT id, titulo, texto FROM respostas_prontas WHERE ativo ORDER BY titulo"):
        texto = r["texto"]
        for k, v in valores.items():
            texto = texto.replace("{" + k + "}", v)
        out.append({"id": r["id"], "titulo": r["titulo"], "texto": texto})
    return out


# =============================================================================
# Base de conhecimento (equipe)
# =============================================================================
@bp.get("/conhecimento")
@requer("conhecimento")
def lista():
    q = request.args.get("q", "")
    artigos = buscar_artigos(q, so_publicos=False, limite=100)
    return render_template("kb/lista.html", artigos=artigos, q=q)


@bp.route("/conhecimento/novo", methods=["GET", "POST"])
@bp.route("/conhecimento/<int:aid>/editar", methods=["GET", "POST"])
@requer("conhecimento")
def editar(aid=None):
    a = db().one("SELECT * FROM artigos WHERE id = %s", [aid]) if aid else None
    if aid and not a:
        abort(404)
    if request.method == "POST":
        f = request.form
        if f.get("acao") == "excluir" and a:
            db().run("DELETE FROM artigos WHERE id = %s", [aid])
            flash("Artigo excluído.", "ok")
            return redirect(url_for("kb.lista"))
        titulo, conteudo = (f.get("titulo") or "").strip(), (f.get("conteudo") or "").strip()
        if not titulo or not conteudo:
            flash("Informe título e conteúdo.", "erro")
            a = {**(a or {}), **f.to_dict(), "publico": f.get("publico") == "1"}
        else:
            vals = [titulo, conteudo, (f.get("categoria") or "").strip() or None,
                    (f.get("palavras") or "").strip() or None, f.get("publico") == "1",
                    (f.get("ativo") == "1") if f.get("ativo_enviado") else True]
            if a:
                db().run("""UPDATE artigos SET titulo=%s, conteudo=%s, categoria=%s, palavras=%s, publico=%s, ativo=%s,
                                   atualizado_em = now() WHERE id = %s""", vals + [aid])
            else:
                aid = db().val("""INSERT INTO artigos (titulo, conteudo, categoria, palavras, publico, ativo, criado_por)
                                  VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""", vals + [g.usuario["id"]])
            flash("Artigo salvo.", "ok")
            return redirect(url_for("kb.ver", aid=aid))
    categorias = [r["categoria"] for r in db().all("SELECT DISTINCT categoria FROM artigos WHERE categoria IS NOT NULL ORDER BY 1")]
    return render_template("kb/editar.html", a=a or {"ativo": True}, categorias=categorias)


@bp.get("/conhecimento/<int:aid>")
@login_obrigatorio
def ver(aid):
    eh_cliente = g.usuario["perfil"] == "cliente"
    if not eh_cliente and not pode("conhecimento") and not pode("chamados"):
        abort(403)
    a = db().one("SELECT a.*, u.nome AS autor FROM artigos a LEFT JOIN usuarios u ON u.id = a.criado_por WHERE a.id = %s",
                 [aid])
    if not a or (eh_cliente and not (a["publico"] and a["ativo"])):
        abort(404)
    db().run("UPDATE artigos SET visualizacoes = visualizacoes + 1 WHERE id = %s", [aid])
    return render_template("kb/ver.html", a=a, eh_cliente=eh_cliente)


@bp.post("/conhecimento/<int:aid>/util")
@login_obrigatorio
def util(aid):
    col = "util_sim" if request.form.get("util") == "1" else "util_nao"
    db().run(f"UPDATE artigos SET {col} = {col} + 1 WHERE id = %s", [aid])
    flash("Obrigado pela avaliação!", "ok")
    return redirect(url_for("kb.ver", aid=aid))


@bp.get("/conhecimento/buscar.json")
@login_obrigatorio
def buscar_json():
    """Sugestões ao digitar (portal: só artigos públicos)."""
    so_pub = g.usuario["perfil"] == "cliente"
    if not so_pub and not (pode("conhecimento") or pode("chamados")):
        abort(403)
    itens = buscar_artigos(request.args.get("q", ""), so_publicos=so_pub, limite=5) if len(request.args.get("q", "")) >= 4 else []
    return jsonify([{"id": i["id"], "titulo": i["titulo"], "url": url_for("kb.ver", aid=i["id"])} for i in itens])


# =============================================================================
# Ajuda no portal
# =============================================================================
@bp.get("/portal/ajuda")
@cliente
def ajuda():
    q = request.args.get("q", "")
    artigos = buscar_artigos(q, so_publicos=True, limite=50)
    return render_template("kb/ajuda.html", artigos=artigos, q=q)
