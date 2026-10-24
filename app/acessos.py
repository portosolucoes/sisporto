"""Controle de acesso por rotina.

Cada perfil tem um padrão; o administrador pode liberar ou bloquear rotinas
para um usuário específico (tabela `permissoes`). O perfil admin tem acesso total.
"""
from __future__ import annotations

import functools

from flask import abort, g

from .auth import login_obrigatorio
from .db import db

# rotina: (descrição, perfis que têm acesso por padrão)
ROTINAS = {
    "chamados":      ("Chamados – ver, abrir e atender", {"tecnico", "financeiro"}),
    "horas":         ("Lançar horas (em chamados e contratos)", {"tecnico"}),
    "aprovar_horas": ("Aprovar / rejeitar horas", {"financeiro"}),
    "relatorios":    ("Relatórios operacionais (apontamentos, SLA, chamados, produtividade, contratos, satisfação)", {"financeiro"}),
    "relatorios_financeiros": ("Relatórios financeiros (pré-faturamento e rentabilidade)", {"financeiro"}),
    "clientes":      ("Clientes, contratos e usuários de clientes", {"financeiro"}),
    "importar":      ("Importar planilha de clientes", {"financeiro"}),
    "emails":        ("E-mails não processados", {"tecnico"}),
    "config":        ("Configurações: SLA, tipos de serviço e logo", set()),
    "equipe":        ("Recursos, acessos e senhas", set()),
}


def _overrides(usuario_id):
    cache = g.setdefault("_permissoes", {})
    if usuario_id not in cache:
        cache[usuario_id] = {r["rotina"]: r["permitido"] for r in
                             db().all("SELECT rotina, permitido FROM permissoes WHERE usuario_id = %s", [usuario_id])}
    return cache[usuario_id]


def padrao(perfil, rotina):
    return perfil == "admin" or perfil in ROTINAS[rotina][1]


def pode(rotina, usuario=None):
    u = usuario or g.get("usuario")
    if not u or u["perfil"] == "cliente" or rotina not in ROTINAS:
        return False
    if u["perfil"] == "admin":
        return True
    return _overrides(u["id"]).get(rotina, padrao(u["perfil"], rotina))


def eh_master(u) -> bool:
    """O administrador master (contato@portosolucoes.com.br) não pode ser desativado nem rebaixado."""
    from flask import current_app
    return bool(u) and u.get("perfil") != "cliente" and \
        (u.get("email") or "").lower() == current_app.config.get("ADMIN_MASTER", "")


def requer(rotina):
    def deco(f):
        @functools.wraps(f)
        @login_obrigatorio
        def w(*a, **k):
            if not pode(rotina):
                abort(403)
            return f(*a, **k)
        return w
    return deco
