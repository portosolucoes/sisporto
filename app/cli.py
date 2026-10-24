"""Comandos de manutenção:  flask --app app <comando>

  banco-criar        aplica db/*.sql numa base vazia (estrutura + dados iniciais)
  banco-atualizar    cria (se vazio) ou aplica migrações pendentes – usado na publicação
  definir-senha      define senha de um usuário (ex.: primeiro acesso do admin)
  criar-admin        cria/corrige um administrador e gera senha temporária
  demo               carrega clientes, contratos e chamados de exemplo
"""
from __future__ import annotations

import pathlib
import secrets

import click
from werkzeug.security import generate_password_hash

from .db import db
from .util import senha_temporaria

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ARQUIVOS = ["001_schema.sql", "002_funcoes_sla.sql", "003_views.sql", "010_dados_iniciais.sql"]


def migracoes():
    """Arquivos db/NNN_*.sql além da base (020 em diante), em ordem."""
    return sorted(p.name for p in (RAIZ / "db").glob("[0-9][0-9][0-9]_*.sql") if p.name not in ARQUIVOS)


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


def _executar_arquivo(nome):
    texto = (RAIZ / "db" / nome).read_text(encoding="utf-8")
    for cmd in _sql_statements(texto):
        db().run(cmd.replace("%", "%%"))


def _tabela_migracoes():
    db().run("""CREATE TABLE IF NOT EXISTS schema_migracoes (
                  arquivo text PRIMARY KEY, aplicado_em timestamptz NOT NULL DEFAULT now())""")


def aplicar_sql(arquivos=ARQUIVOS):
    """Base vazia: estrutura + dados iniciais + todas as migrações."""
    _tabela_migracoes()
    for nome in list(arquivos) + migracoes():
        _executar_arquivo(nome)
        db().run("INSERT INTO schema_migracoes (arquivo) VALUES (%s) ON CONFLICT DO NOTHING", [nome])
    db().commit()


def atualizar_banco():
    """Aplica só as migrações pendentes. Devolve a lista aplicada."""
    _tabela_migracoes()
    if not db().val("SELECT count(*) FROM schema_migracoes") and db().val("SELECT to_regclass('public.chamados')"):
        for nome in ARQUIVOS:  # banco criado antes do controle de migrações
            db().run("INSERT INTO schema_migracoes (arquivo) VALUES (%s)", [nome])
    feitas = {r["arquivo"] for r in db().all("SELECT arquivo FROM schema_migracoes")}
    aplicadas = []
    for nome in migracoes():
        if nome not in feitas:
            _executar_arquivo(nome)
            db().run("INSERT INTO schema_migracoes (arquivo) VALUES (%s)", [nome])
            aplicadas.append(nome)
    db().commit()
    return aplicadas


def registrar(app):
    @app.cli.command("banco-criar")
    def banco_criar():
        """Cria a estrutura do banco e os dados iniciais."""
        if db().val("SELECT to_regclass('public.chamados')"):
            raise click.ClickException("O banco já tem as tabelas do SisPorto. Nada foi alterado.")
        aplicar_sql()
        click.echo("Banco criado.")

    @app.cli.command("banco-atualizar")
    def banco_atualizar():
        """Cria o banco se estiver vazio; senão aplica as migrações pendentes."""
        if not db().val("SELECT to_regclass('public.chamados')"):
            aplicar_sql()
            click.echo("Banco criado.")
            return
        feitas = atualizar_banco()
        click.echo("Migrações aplicadas: " + (", ".join(feitas) if feitas else "nenhuma (já atualizado)"))

    @app.cli.command("definir-senha")
    @click.argument("email")
    def definir_senha(email):
        """Gera uma senha temporária para o usuário EMAIL."""
        senha = senha_temporaria()
        n = db().run("UPDATE usuarios SET senha_hash = %s WHERE email = %s",
                     [generate_password_hash(senha), email.lower()])
        db().commit()
        if not n:
            raise click.ClickException("Usuário não encontrado.")
        click.echo(f"Senha temporária de {email}: {senha}")

    @app.cli.command("criar-admin")
    @click.argument("email")
    @click.option("--nome", default="Administrador", help="Nome exibido no sistema")
    def criar_admin(email, nome):
        """Cria (ou corrige) EMAIL como administrador ativo e gera senha temporária."""
        senha = senha_temporaria()
        db().run("""INSERT INTO usuarios (nome, email, perfil, senha_hash)
                    VALUES (%s, %s, 'admin', %s)
                    ON CONFLICT (email) DO UPDATE
                    SET perfil = 'admin', cliente_id = NULL, ativo = true, senha_hash = EXCLUDED.senha_hash""",
                 [nome, email.strip().lower(), generate_password_hash(senha)])
        db().commit()
        click.echo(f"Administrador {email} pronto. Senha temporária de {email}: {senha}")

    @app.cli.command("demo")
    def demo():
        """Carrega dados de exemplo (use só em teste)."""
        from .demo import carregar
        carregar()
        db().commit()
        click.echo("Dados de exemplo carregados.")
