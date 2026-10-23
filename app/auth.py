"""Login: e-mail e senha (clientes e equipe) e Google (equipe @portosolucoes.com.br)."""
from __future__ import annotations

import functools
import secrets
import time
from urllib.parse import urlencode

import jwt
import requests
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import db

bp = Blueprint("auth", __name__)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"

SENHA_MINIMA = 10


def carregar_usuario():
    g.usuario = None
    uid = session.get("uid")
    if not uid or request.endpoint in ("static", "saude"):
        return
    u = db().one("SELECT id, nome, email, perfil, cliente_id, ativo FROM usuarios WHERE id = %s", [uid])
    if not u or not u["ativo"]:
        session.clear()
        return
    g.usuario = u
    db().set_usuario(u["id"])


def _entrar(u):
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["csrf"] = secrets.token_urlsafe(32)
    db().run("UPDATE usuarios SET ultimo_acesso = now() WHERE id = %s", [u["id"]])


def destino_inicial(u):
    return url_for("portal.painel") if u["perfil"] == "cliente" else url_for("equipe.painel")


# --- decoradores --------------------------------------------------------------
def login_obrigatorio(f):
    @functools.wraps(f)
    def w(*a, **k):
        if not g.get("usuario"):
            return redirect(url_for("auth.login", proximo=request.full_path))
        return f(*a, **k)
    return w


def perfis(*permitidos):
    def deco(f):
        @functools.wraps(f)
        @login_obrigatorio
        def w(*a, **k):
            if g.usuario["perfil"] not in permitidos:
                abort(403)
            return f(*a, **k)
        return w
    return deco


equipe = perfis("admin", "tecnico", "financeiro")
admin = perfis("admin")


# --- rotas --------------------------------------------------------------------
@bp.get("/")
def inicio():
    if g.get("usuario"):
        return redirect(destino_inicial(g.usuario))
    return redirect(url_for("auth.login"))


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        u = db().one("SELECT id, perfil, senha_hash, ativo FROM usuarios WHERE email = %s", [email])
        if u and u["ativo"] and u["senha_hash"] and check_password_hash(u["senha_hash"], senha):
            _entrar(u)
            proximo = request.args.get("proximo") or ""
            if proximo.startswith("/") and not proximo.startswith("//"):
                return redirect(proximo)
            return redirect(destino_inicial(u))
        time.sleep(0.5)
        flash("E-mail ou senha incorretos.", "erro")
    google = bool(current_app.config["GOOGLE_CLIENT_ID"])
    return render_template("login.html", google=google)


@bp.post("/sair")
def sair():
    session.clear()
    flash("Você saiu do sistema.", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/conta/senha", methods=["GET", "POST"])
@login_obrigatorio
def trocar_senha():
    if request.method == "POST":
        atual = request.form.get("atual") or ""
        nova = request.form.get("nova") or ""
        conf = request.form.get("confirmacao") or ""
        h = db().val("SELECT senha_hash FROM usuarios WHERE id = %s", [g.usuario["id"]])
        if h and not check_password_hash(h, atual):
            flash("Senha atual incorreta.", "erro")
        elif len(nova) < SENHA_MINIMA:
            flash(f"A nova senha precisa ter pelo menos {SENHA_MINIMA} caracteres.", "erro")
        elif nova != conf:
            flash("A confirmação não confere.", "erro")
        else:
            db().run("UPDATE usuarios SET senha_hash = %s WHERE id = %s",
                     [generate_password_hash(nova), g.usuario["id"]])
            flash("Senha alterada.", "ok")
            return redirect(destino_inicial(g.usuario))
    return render_template("senha.html")


@bp.get("/entrar/google")
def google_inicio():
    cfg = current_app.config
    if not cfg["GOOGLE_CLIENT_ID"]:
        abort(404)
    session["oauth_state"] = secrets.token_urlsafe(24)
    params = {
        "client_id": cfg["GOOGLE_CLIENT_ID"],
        "redirect_uri": url_for("auth.google_retorno", _external=True),
        "response_type": "code",
        "scope": "openid email profile",
        "state": session["oauth_state"],
        "hd": cfg["DOMINIO_EQUIPE"],
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTH}?{urlencode(params)}")


@bp.get("/entrar/google/retorno")
def google_retorno():
    cfg = current_app.config
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400, "Sessão de login expirada. Tente de novo.")
    r = requests.post(GOOGLE_TOKEN, timeout=10, data={
        "code": request.args.get("code", ""),
        "client_id": cfg["GOOGLE_CLIENT_ID"],
        "client_secret": cfg["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": url_for("auth.google_retorno", _external=True),
        "grant_type": "authorization_code",
    })
    if r.status_code != 200:
        abort(400, "O Google recusou o login.")
    # id_token recebido direto do Google por TLS: assinatura dispensável (OIDC Core 3.1.3.7)
    info = jwt.decode(r.json()["id_token"], options={"verify_signature": False})
    if info.get("aud") != cfg["GOOGLE_CLIENT_ID"] or not info.get("email_verified") \
            or info.get("hd") != cfg["DOMINIO_EQUIPE"]:
        abort(403)
    u = db().one("SELECT id, perfil, ativo FROM usuarios WHERE email = %s AND perfil <> 'cliente'",
                 [info["email"].lower()])
    if not u or not u["ativo"]:
        flash("Sua conta Google não está cadastrada na equipe. Fale com o administrador.", "erro")
        return redirect(url_for("auth.login"))
    _entrar(u)
    return redirect(destino_inicial(u))
