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
    "chamados_gerir": ("Alterar contrato, responsável, status e prioridade dos chamados", {"tecnico", "financeiro"}),
    "horas":         ("Lançar horas (em chamados e contratos) e usar o cronômetro", {"tecnico"}),
    "relatorios":    ("Relatórios operacionais (SLA, chamados, contratos, satisfação; horas só as próprias)", {"financeiro"}),
    "relatorios_financeiros": ("Relatórios financeiros (pré-faturamento e rentabilidade)", {"financeiro"}),
    "dashboard":     ("Dashboard gerencial (indicadores do mês)", {"financeiro"}),
    "clientes":      ("Clientes e usuários de clientes", {"financeiro"}),
    "contratos":     ("Contratos (incluir, editar, horas liberadas)", {"financeiro"}),
    "importar":      ("Importar planilha de clientes", {"financeiro"}),
    "emails":        ("E-mails não processados", {"tecnico"}),
    "conhecimento":  ("Base de conhecimento – criar e editar artigos", {"tecnico", "financeiro"}),
    "config":        ("Configurações: SLA, tipos de serviço, integrações e logo", set()),
}

# rotinas exclusivas do administrador (aparecem no cadastro do recurso, mas não podem ser liberadas a outros perfis)
SO_ADMIN = {
    "aprovacao":    "Aprovação de horas dos recursos",
    "apontamentos": "Editar/gerar apontamentos de qualquer recurso",
    "saldos":       "Saldos de horas e rateio entre chamados",
    "recursos":     "Recursos, perfis, acessos e senhas",
    "consultas":    "Consultas SQL e dicionário de dados",
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
    if not u or u["perfil"] == "cliente":
        return False
    if u["perfil"] == "admin":
        return True
    if rotina not in ROTINAS:  # "admin" e rotinas de SO_ADMIN
        return False
    return _overrides(u["id"]).get(rotina, padrao(u["perfil"], rotina))


def eh_master(u) -> bool:
    """O administrador master (contato@portosolucoes.com.br) não pode ser desativado nem rebaixado."""
    from flask import current_app
    return bool(u) and u.get("perfil") != "cliente" and \
        (u.get("email") or "").lower() == current_app.config.get("ADMIN_MASTER", "")


def eh_admin(u=None) -> bool:
    u = u or g.get("usuario")
    return bool(u) and u["perfil"] == "admin"


def salvar_permissoes(u, marcadas):
    """Grava as rotinas marcadas para um recurso não-admin (só guarda o que difere do padrão do perfil)."""
    for r in ROTINAS:
        marcado = r in marcadas
        if marcado == padrao(u["perfil"], r):
            db().run("DELETE FROM permissoes WHERE usuario_id = %s AND rotina = %s", [u["id"], r])
        else:
            db().run("""INSERT INTO permissoes (usuario_id, rotina, permitido) VALUES (%s,%s,%s)
                        ON CONFLICT (usuario_id, rotina) DO UPDATE SET permitido = EXCLUDED.permitido""",
                     [u["id"], r, marcado])
    g.pop("_permissoes", None)


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
