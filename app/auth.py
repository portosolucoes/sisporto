"""Login: e-mail e senha (clientes e equipe) e Google (equipe @portosolucoes.com.br)."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import struct
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
    u = db().one("SELECT id, codigo, nome, email, perfil, cliente_id, ativo FROM usuarios WHERE id = %s", [uid])
    if not u or not u["ativo"]:
        session.clear()
        return
    g.usuario = u
    db().set_usuario(u["id"])
    if session.get("senha_provisoria") and request.endpoint not in ("auth.trocar_senha", "auth.sair", "logo"):
        flash("Você entrou com uma senha temporária. Crie a sua senha para continuar.", "ok")
        return redirect(url_for("auth.trocar_senha"))


def _entrar(u):
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    session["csrf"] = secrets.token_urlsafe(32)
    if db().val("UPDATE usuarios SET ultimo_acesso = now() WHERE id = %s RETURNING senha_provisoria", [u["id"]]):
        session["senha_provisoria"] = True  # entrou com senha temporária: troca obrigatória


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
        from .limpeza import excedeu, registrar
        ip = request.remote_addr or "?"
        alvo = (request.form.get("email") or "").strip().lower()
        if excedeu(f"login-ip:{ip}", 20, 15) or excedeu(f"login-usuario:{alvo}", 8, 15):
            flash("Muitas tentativas sem sucesso. Aguarde 15 minutos ou use “Esqueci minha senha”.", "erro")
            return render_template("login.html", google=bool(current_app.config["GOOGLE_CLIENT_ID"])), 429
        ident = (request.form.get("email") or "").strip()
        senha = (request.form.get("senha") or "").strip()  # colar do terminal pode trazer espaços
        if "@" in ident:
            candidatos = db().all("""SELECT id, perfil, senha_hash FROM usuarios
                                      WHERE email = %s AND ativo AND senha_hash IS NOT NULL""", [ident.lower()])
        else:
            candidatos = db().all("""SELECT id, perfil, senha_hash FROM usuarios
                                      WHERE upper(codigo) = upper(%s) AND ativo AND senha_hash IS NOT NULL""", [ident])
        certos = [c for c in candidatos if check_password_hash(c["senha_hash"], senha)]
        if len(certos) > 1:
            flash("Este e-mail tem mais de um acesso com a mesma senha. Entre com o seu código de usuário "
                  "(ex.: R0001 ou C0001).", "erro")
            return render_template("login.html", google=bool(current_app.config["GOOGLE_CLIENT_ID"]))
        if certos:
            u = certos[0]
            if u["perfil"] != "cliente" and db().val("SELECT totp_ativo FROM usuarios WHERE id = %s", [u["id"]]):
                proximo = request.args.get("proximo") or ""
                session.clear()
                session["pre_2fa"] = u["id"]
                session["pre_2fa_proximo"] = proximo
                session["pre_2fa_tentativas"] = 0
                session["csrf"] = secrets.token_urlsafe(32)
                return redirect(url_for("auth.login_2fa"))
            _entrar(u)
            proximo = request.args.get("proximo") or ""
            if proximo.startswith("/") and not proximo.startswith("//"):
                return redirect(proximo)
            return redirect(destino_inicial(u))
        registrar(f"login-ip:{ip}")
        registrar(f"login-usuario:{alvo}")
        time.sleep(0.5)
        flash("Usuário ou senha incorretos.", "erro")
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
        provisoria = session.get("senha_provisoria")
        if h and not provisoria and not check_password_hash(h, atual.strip()):
            flash("Senha atual incorreta.", "erro")
        elif len(nova) < SENHA_MINIMA:
            flash(f"A nova senha precisa ter pelo menos {SENHA_MINIMA} caracteres.", "erro")
        elif nova != conf:
            flash("A confirmação não confere.", "erro")
        elif h and check_password_hash(h, nova):
            flash("A nova senha precisa ser diferente da atual.", "erro")
        else:
            db().run("UPDATE usuarios SET senha_hash = %s, senha_provisoria = false WHERE id = %s",
                     [generate_password_hash(nova), g.usuario["id"]])
            session.pop("senha_provisoria", None)
            flash("Senha alterada.", "ok")
            return redirect(destino_inicial(g.usuario))
    return render_template("senha.html", provisoria=session.get("senha_provisoria"))


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


# --- recuperação de senha ------------------------------------------------------
VALIDADE_TOKEN_MIN = 60
MAX_PEDIDOS_HORA = 3
MSG_RECUPERAR = ("Se o e-mail estiver cadastrado, você receberá em alguns minutos um link para criar uma nova "
                 "senha. Confira também a caixa de spam.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@bp.route("/recuperar-senha", methods=["GET", "POST"])
def recuperar_senha():
    email = (request.values.get("email") or "").strip().lower()
    if request.method == "POST":
        if "@" not in email:
            flash("Digite o seu e-mail para recuperar a senha.", "erro")
            return render_template("recuperar.html", email=email)
        from .limpeza import excedeu, registrar
        ip = request.remote_addr or "?"
        if excedeu(f"senha-ip:{ip}", 10, 60):
            flash("Muitos pedidos deste local. Tente de novo em 1 hora.", "erro")
            return render_template("recuperar.html", email=email), 429
        registrar(f"senha-ip:{ip}")
        for u in db().all("SELECT id, nome, email, codigo FROM usuarios WHERE email = %s AND ativo", [email]):
            enviar_link_senha(u)
        time.sleep(0.3)
        flash(MSG_RECUPERAR, "ok")
        return redirect(url_for("auth.login"))
    return render_template("recuperar.html", email=email)


def enviar_link_senha(u, forcar=False):
    """Gera token de uso único e enfileira o e-mail com o link. Respeita o limite por hora."""
    if not forcar:
        recentes = db().val("""SELECT count(*) FROM senha_tokens
                                WHERE usuario_id = %s AND criado_em > now() - interval '1 hour'""", [u["id"]])
        if recentes >= MAX_PEDIDOS_HORA:
            return False
    token = secrets.token_urlsafe(32)
    db().run("""INSERT INTO senha_tokens (usuario_id, token_hash, expira_em, ip)
                VALUES (%s, %s, now() + make_interval(mins => %s), %s)""",
             [u["id"], _hash_token(token), VALIDADE_TOKEN_MIN, request.remote_addr])
    link = url_for("auth.redefinir_senha", token=token, _external=True)
    corpo = (f"Olá, {u['nome']}.\n\nRecebemos um pedido para criar uma nova senha no SisPorto "
             f"para o usuário {u.get('codigo') or ''}.\n"
             f"Para continuar, abra o link abaixo (válido por {VALIDADE_TOKEN_MIN} minutos e uso único):\n\n"
             f"{link}\n\nSe não foi você, ignore este e-mail: sua senha atual continua valendo.\n\n"
             "—\nSuporte Porto Soluções Corporativas")
    db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento)
                VALUES (%s, 'SisPorto – criar nova senha', %s, 'senha')""", [u["email"], corpo])
    return True


def _token_valido(token):
    return db().one("""SELECT t.id, t.usuario_id, u.email FROM senha_tokens t JOIN usuarios u ON u.id = t.usuario_id
                        WHERE t.token_hash = %s AND t.usado_em IS NULL AND t.expira_em > now() AND u.ativo""",
                    [_hash_token(token)])


@bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    t = _token_valido(token)
    if not t:
        flash("Este link de recuperação expirou ou já foi usado. Peça um novo.", "erro")
        return redirect(url_for("auth.recuperar_senha"))
    if request.method == "POST":
        nova = request.form.get("nova") or ""
        if len(nova) < SENHA_MINIMA:
            flash(f"A nova senha precisa ter pelo menos {SENHA_MINIMA} caracteres.", "erro")
        elif nova != (request.form.get("confirmacao") or ""):
            flash("A confirmação não confere.", "erro")
        else:
            db().run("UPDATE usuarios SET senha_hash = %s, senha_provisoria = false WHERE id = %s",
                     [generate_password_hash(nova), t["usuario_id"]])
            db().run("UPDATE senha_tokens SET usado_em = now() WHERE usuario_id = %s AND usado_em IS NULL",
                     [t["usuario_id"]])
            session.clear()
            flash("Senha alterada. Entre com a nova senha.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("redefinir.html", email=t["email"])


# --- verificação em duas etapas (TOTP, compatível com Google Authenticator) ---------------
def totp_novo_segredo() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_codigo(segredo: str, momento: float | None = None, passo: int = 30) -> str:
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8))
    contador = int((momento if momento is not None else time.time()) // passo)
    h = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_valido(segredo: str, codigo: str) -> bool:
    codigo = (codigo or "").replace(" ", "")
    if not (segredo and codigo.isdigit() and len(codigo) == 6):
        return False
    agora = time.time()
    return any(hmac.compare_digest(totp_codigo(segredo, agora + d * 30), codigo) for d in (-1, 0, 1))


@bp.route("/entrar/2fa", methods=["GET", "POST"])
def login_2fa():
    uid = session.get("pre_2fa")
    if not uid:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        u = db().one("SELECT id, perfil, totp_segredo, ativo FROM usuarios WHERE id = %s", [uid])
        if u and u["ativo"] and totp_valido(u["totp_segredo"], request.form.get("codigo")):
            proximo = session.get("pre_2fa_proximo") or ""
            _entrar(u)
            if proximo.startswith("/") and not proximo.startswith("//"):
                return redirect(proximo)
            return redirect(destino_inicial(u))
        session["pre_2fa_tentativas"] = session.get("pre_2fa_tentativas", 0) + 1
        time.sleep(0.5)
        if session["pre_2fa_tentativas"] >= 5:
            session.clear()
            flash("Muitas tentativas. Entre novamente com usuário e senha.", "erro")
            return redirect(url_for("auth.login"))
        flash("Código inválido. Confira o horário do celular e tente o código atual.", "erro")
    return render_template("login_2fa.html")


@bp.route("/conta/2fa", methods=["GET", "POST"])
@login_obrigatorio
def conta_2fa():
    if g.usuario["perfil"] == "cliente":
        abort(404)
    atual = db().one("SELECT totp_ativo, totp_segredo FROM usuarios WHERE id = %s", [g.usuario["id"]])
    if request.method == "POST":
        codigo = request.form.get("codigo")
        if atual["totp_ativo"]:
            if totp_valido(atual["totp_segredo"], codigo):
                db().run("UPDATE usuarios SET totp_ativo = false, totp_segredo = NULL WHERE id = %s", [g.usuario["id"]])
                flash("Verificação em duas etapas desativada.", "ok")
            else:
                flash("Código inválido.", "erro")
        else:
            novo = session.get("totp_novo")
            if novo and totp_valido(novo, codigo):
                db().run("UPDATE usuarios SET totp_segredo = %s, totp_ativo = true WHERE id = %s",
                         [novo, g.usuario["id"]])
                session.pop("totp_novo", None)
                flash("Verificação em duas etapas ativada. A partir de agora o login pedirá o código do app.", "ok")
            else:
                flash("Código inválido. Digite o código de 6 dígitos que aparece no app agora.", "erro")
        return redirect(url_for("auth.conta_2fa"))
    qr = uri = segredo = None
    if not atual["totp_ativo"]:
        segredo = session.get("totp_novo") or totp_novo_segredo()
        session["totp_novo"] = segredo
        from urllib.parse import quote
        uri = (f"otpauth://totp/SisPorto:{quote(g.usuario['email'])}?secret={segredo}"
               f"&issuer=SisPorto%20Porto%20Solu%C3%A7%C3%B5es&digits=6&period=30")
        try:
            import segno
            qr = segno.make(uri, error="m").svg_inline(scale=5, dark="#14263d")
        except ImportError:
            qr = None
    grupos = " ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4)) if segredo else None
    return render_template("conta_2fa.html", ativo=atual["totp_ativo"], qr=qr, uri=uri, segredo=grupos)
