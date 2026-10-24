"""Limpeza automática (1x por dia via /integracao/tarefas): mantém o banco pequeno e o custo previsível.

- anexos com mais de 90 dias e já copiados ao Google Drive saem do banco (o link do Drive continua no chamado)
- e-mails já enviados há mais de 180 dias
- consultas SQL registradas há mais de 1 ano
- auditoria com mais de 2 anos
- links de senha usados/vencidos há mais de 30 dias; tentativas com mais de 1 dia
"""
from __future__ import annotations

import datetime as dt

from .db import db
from .util import TZ

REGRAS = [
    ("anexos_arquivados", """UPDATE chamado_anexos SET arquivo = NULL, arquivado_em = now()
                              WHERE arquivo IS NOT NULL AND drive_arquivo_id IS NOT NULL
                                AND criado_em < now() - interval '90 days'"""),
    ("emails_enviados", "DELETE FROM emails_saida WHERE enviado_em < now() - interval '180 days'"),
    ("consultas_log", "DELETE FROM consultas_log WHERE executado_em < now() - interval '1 year'"),
    ("auditoria", "DELETE FROM auditoria WHERE em < now() - interval '2 years'"),
    ("senha_tokens", "DELETE FROM senha_tokens WHERE expira_em < now() - interval '30 days'"),
    ("tentativas", "DELETE FROM tentativas WHERE em < now() - interval '1 day'"),
]


def limpar(forcar=False) -> dict:
    hoje = dt.datetime.now(TZ).date().isoformat()
    from .integracoes import config, salvar_config
    if not forcar and config("limpeza_dia") == hoje:
        return {}
    feito = {nome: db().run(sql) for nome, sql in REGRAS}
    salvar_config("limpeza_dia", hoje)
    return feito


def excedeu(chave: str, maximo: int, minutos: int) -> bool:
    return db().val("SELECT count(*) FROM tentativas WHERE chave = %s AND em > now() - make_interval(mins => %s)",
                    [chave, minutos]) >= maximo


def registrar(chave: str) -> None:
    db().run("INSERT INTO tentativas (chave) VALUES (%s)", [chave])
