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
    """Divide um arquivo SQL em comandos, respeitando textos entre aspas, blocos $$...$$ e comentários."""
    cmds, atual, em_dolar, em_aspas, i = [], [], False, False, 0
    while i < len(texto):
        if not em_dolar and texto[i] == "'":  # texto entre aspas simples ('' é aspa escapada)
            em_aspas = not em_aspas
            atual.append("'"); i += 1
            continue
        if em_aspas:
            atual.append(texto[i]); i += 1
            continue
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
        garantir_master()

    def garantir_master():
        """O e-mail ADMIN_MASTER é sempre administrador ativo."""
        n = db().val("""WITH x AS (UPDATE usuarios SET perfil = 'admin', ativo = true
                                    WHERE lower(email) = %s AND perfil <> 'cliente' AND (perfil <> 'admin' OR NOT ativo)
                                    RETURNING 1) SELECT count(*) FROM x""", [app.config["ADMIN_MASTER"]])
        db().commit()
        if n:
            click.echo(f"Administrador master {app.config['ADMIN_MASTER']} restaurado.")

    @app.cli.command("definir-senha")
    @click.argument("email")
    def definir_senha(email):
        """Gera uma senha temporária para o usuário EMAIL."""
        senha = senha_temporaria()
        n = db().run("UPDATE usuarios SET senha_hash = %s, senha_provisoria = true WHERE email = %s OR upper(codigo) = upper(%s)",
                     [generate_password_hash(senha), email.lower(), email])
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
        email = email.strip().lower()
        h = generate_password_hash(senha)
        codigo = db().val("""UPDATE usuarios SET perfil = 'admin', ativo = true, senha_hash = %s, senha_provisoria = true
                              WHERE email = %s AND perfil <> 'cliente' RETURNING codigo""", [h, email])
        if not codigo:
            codigo = db().val("""INSERT INTO usuarios (nome, email, perfil, senha_hash, senha_provisoria) VALUES (%s, %s, 'admin', %s, true)
                                 RETURNING codigo""", [nome, email, h])
        db().commit()
        click.echo(f"Administrador {email} (código {codigo}) pronto. Senha temporária de {email}: {senha}")

    @app.cli.command("dicionario-pdf")
    @click.argument("arquivo", default="docs/dicionario-de-dados.pdf")
    def dicionario_pdf(arquivo):
        """Gera o dicionário de dados do banco em PDF."""
        import datetime as dt
        from . import dicionario, pdf
        caminho = pathlib.Path(arquivo)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(pdf.gerar("SisPorto – Dicionário de dados",
                                      f"Estrutura do banco em {dt.date.today():%d/%m/%Y}",
                                      dicionario.estrutura_pdf(dicionario.ler())))
        click.echo(f"Dicionário gerado: {caminho}")

    @app.cli.command("demo")
    def demo():
        """Carrega dados de exemplo (use só em teste)."""
        from .demo import carregar
        carregar()
        db().commit()
        click.echo("Dados de exemplo carregados.")
