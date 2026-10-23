"""SisPorto – aplicação web (Flask)."""
from __future__ import annotations

import os
import secrets

from flask import Flask, abort, g, request, session

from . import db as dbmod
from . import util


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE_URL=os.environ.get("DATABASE_URL", "postgresql:///sisporto"),
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID", ""),
        GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        DOMINIO_EQUIPE=os.environ.get("DOMINIO_EQUIPE", "portosolucoes.com.br"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SEGURO", "1") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    if os.environ.get("ATRAS_DE_PROXY", "1") == "1":  # Cloud Run: https e host corretos nos links
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.after_request(dbmod.confirmar)
    app.teardown_appcontext(dbmod.fechar)
    util.registrar_filtros(app)

    # --- proteção CSRF simples (token por sessão em todo POST) -------------
    @app.before_request
    def _csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST" and not app.config.get("TESTING_SEM_CSRF"):
            enviado = request.form.get("csrf") or request.headers.get("X-CSRF")
            if not enviado or not secrets.compare_digest(enviado, session["csrf"]):
                abort(400, "Formulário expirado. Recarregue a página.")

    @app.context_processor
    def _ctx():
        return {"csrf": session.get("csrf", ""), "usuario": g.get("usuario")}

    from .auth import bp as auth_bp, carregar_usuario
    from .rotas_equipe import bp as equipe_bp
    from .rotas_portal import bp as portal_bp

    app.before_request(carregar_usuario)
    app.register_blueprint(auth_bp)
    app.register_blueprint(equipe_bp)
    app.register_blueprint(portal_bp, url_prefix="/portal")

    @app.get("/saude")
    def saude():
        dbmod.db().val("SELECT 1")
        return {"ok": True, "driver": dbmod._DRIVER}

    @app.errorhandler(403)
    def _proibido(e):
        return util.pagina_erro("Acesso negado", "Você não tem permissão para ver esta página."), 403

    @app.errorhandler(404)
    def _nao_encontrado(e):
        return util.pagina_erro("Não encontrado", "A página ou registro não existe."), 404

    @app.errorhandler(400)
    def _invalido(e):
        return util.pagina_erro("Requisição inválida", getattr(e, "description", "")), 400

    from .cli import registrar as registrar_cli
    registrar_cli(app)
    return app
