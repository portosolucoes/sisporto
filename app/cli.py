"""Comandos de manutenção:  flask --app app <comando>

  banco-criar        aplica db/*.sql numa base vazia (estrutura + dados iniciais)
  definir-senha      define senha de um usuário (ex.: primeiro acesso do admin)
  demo               carrega clientes, contratos e chamados de exemplo
"""
from __future__ import annotations

import pathlib
import secrets

import click
from werkzeug.security import generate_password_hash

from .db import db

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ARQUIVOS = ["001_schema.sql", "002_funcoes_sla.sql", "003_views.sql", "010_dados_iniciais.sql"]


def _sql_statements(texto: str):
    """Divide um arquivo SQL em comandos, respeitando blocos $$...$$ e comentários."""
    cmds, atual, em_dolar, i = [], [], False, 0
    while i < len(texto):
        if not em_dolar and texto.startswith("--", i):
            fim = texto.find("\n", i)
            i = len(texto) if fim == -1 else fim
            continue
        if texto.startswith("$$", i):
            em_dolar = not em_dolar
            atual.append("$$"); i += 2
            continue
        ch = texto[i]
        if ch == ";" and not em_dolar:
            cmd = "".join(atual).strip()
            if cmd:
                cmds.append(cmd)
            atual = []
        else:
            atual.append(ch)
        i += 1
    if "".join(atual).strip():
        cmds.append("".join(atual).strip())
    return cmds


def aplicar_sql(arquivos=ARQUIVOS):
    for nome in arquivos:
        texto = (RAIZ / "db" / nome).read_text(encoding="utf-8")
        for cmd in _sql_statements(texto):
            db().run(cmd.replace("%", "%%"))
    db().commit()


def registrar(app):
    @app.cli.command("banco-criar")
    def banco_criar():
        """Cria a estrutura do banco e os dados iniciais."""
        if db().val("SELECT to_regclass('public.chamados')"):
            raise click.ClickException("O banco já tem as tabelas do SisPorto. Nada foi alterado.")
        aplicar_sql()
        click.echo("Banco criado.")

    @app.cli.command("definir-senha")
    @click.argument("email")
    def definir_senha(email):
        """Gera uma senha temporária para o usuário EMAIL."""
        senha = secrets.token_urlsafe(9)
        n = db().run("UPDATE usuarios SET senha_hash = %s WHERE email = %s",
                     [generate_password_hash(senha), email.lower()])
        db().commit()
        if not n:
            raise click.ClickException("Usuário não encontrado.")
        click.echo(f"Senha temporária de {email}: {senha}")

    @app.cli.command("demo")
    def demo():
        """Carrega dados de exemplo (use só em teste)."""
        from .demo import carregar
        carregar()
        db().commit()
        click.echo("Dados de exemplo carregados.")
