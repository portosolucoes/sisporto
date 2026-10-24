"""Avisos automáticos de saldo de horas (80% e 100%) ao cliente e ao gestor da conta.

Roda a cada 5 minutos pelo Apps Script (/integracao/tarefas) e logo depois de cada lançamento de horas.
Cada nível é avisado uma única vez por período (mês no mensal fixo; contrato inteiro nos demais).
"""
from __future__ import annotations

import datetime as dt

from flask import url_for

from .db import db
from .util import TZ, horas_dec

NIVEIS = (100, 80)


def _destinatarios(contrato):
    """Cliente: e-mail financeiro + usuários marcados 'Recebe aviso de saldo'. Porto: gestor da conta (ou admins)."""
    cli = [r["email"] for r in db().all("""SELECT email FROM usuarios WHERE cliente_id = %s AND ativo
                                             AND perfil = 'cliente' AND recebe_alerta_saldo""", [contrato["cliente_id"]])]
    if contrato["email_financeiro"]:
        cli.append(contrato["email_financeiro"])
    porto = [r["email"] for r in db().all("SELECT email FROM usuarios WHERE id = %s AND ativo",
                                          [contrato["responsavel_id"]])] if contrato["responsavel_id"] else []
    if not porto:
        porto = [r["email"] for r in db().all("SELECT email FROM usuarios WHERE perfil = 'admin' AND ativo")]
    return sorted(set(e.lower() for e in cli if e)), sorted(set(e.lower() for e in porto if e))


def verificar_alertas_saldo(contrato_id=None) -> int:
    filtro, p = ("AND s.contrato_id = %s", [contrato_id]) if contrato_id else ("", [])
    linhas = db().all(f"""
        SELECT s.*, ct.tipo::text AS tipo, ct.cliente_id, cl.email_financeiro, cl.responsavel_id,
               coalesce(cl.nome_fantasia, cl.razao_social) AS cliente
          FROM vw_saldo_banco_horas s JOIN contratos ct ON ct.id = s.contrato_id
          JOIN clientes cl ON cl.id = ct.cliente_id
         WHERE ct.ativo AND s.pct_consumido >= 80 {filtro}""", p)
    hoje = dt.datetime.now(TZ).date()
    enviados = 0
    for s in linhas:
        periodo = hoje.replace(day=1) if s["tipo"] == "mensal_fixo" else s["data_inicio"]
        nivel = next(n for n in NIVEIS if s["pct_consumido"] >= n)
        ja = db().val("SELECT max(nivel) FROM saldo_alertas WHERE contrato_id = %s AND periodo = %s",
                      [s["contrato_id"], periodo])
        if ja and ja >= nivel:
            continue
        db().run("""INSERT INTO saldo_alertas (contrato_id, periodo, nivel, pct) VALUES (%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", [s["contrato_id"], periodo, nivel, s["pct_consumido"]])
        if nivel == 100 and not ja:  # pulou direto para 100%: registra o 80% também (não reenvia depois)
            db().run("""INSERT INTO saldo_alertas (contrato_id, periodo, nivel, pct) VALUES (%s,%s,80,%s)
                        ON CONFLICT DO NOTHING""", [s["contrato_id"], periodo, s["pct_consumido"]])
        _enviar(s, nivel)
        enviados += 1
    return enviados


def _enviar(s, nivel):
    cli, porto = _destinatarios(s)
    ref = "deste mês" if s["tipo"] == "mensal_fixo" else "do contrato"
    situacao = (f"atingiu {s['pct_consumido']}% das horas liberadas {ref}" if nivel == 80 else
                f"esgotou as horas liberadas {ref} ({s['pct_consumido']}% consumido)")
    numeros = (f"Horas liberadas: {horas_dec(s['horas_liberadas'])}"
               + (f" (incluindo {horas_dec(s['horas_extras'])} de horas extras aprovadas)" if s["horas_extras"] else "")
               + f"\nConsumidas: {horas_dec(s['horas_consumidas'])}\nSaldo: {horas_dec(max(s['horas_saldo'], 0))}")
    try:
        portal = url_for("portal.painel", _external=True)
        extras = url_for("extras.portal_lista", _external=True)
    except RuntimeError:
        portal = extras = ""
    proximo = ("Para não interromper o atendimento, a Porto Soluções pode solicitar horas extras, que você aprova "
               f"pelo portal: {extras}" if nivel == 100 else
               "Fique tranquilo: avisaremos novamente se o saldo acabar.")
    for email in cli:
        corpo = (f"Olá,\n\nO contrato {s['descricao']} da {s['cliente']} {situacao}.\n\n{numeros}\n\n{proximo}\n\n"
                 f"Acompanhe o consumo no portal: {portal}\n\n—\nPorto Soluções Corporativas")
        db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento) VALUES (%s,%s,%s,'alerta_saldo')""",
                 [email, f"Porto Soluções – saldo de horas em {nivel}% ({s['descricao']})", corpo])
    for email in porto:
        corpo = (f"O contrato {s['descricao']} ({s['cliente']}) {situacao}.\n\n{numeros}\n\n"
                 + ("Avalie solicitar horas extras ao cliente (Horas → Horas extras) para não bloquear os chamados.\n\n"
                    if nivel == 100 else "") + "—\nSisPorto")
        db().run("""INSERT INTO emails_saida (para, assunto, corpo, evento) VALUES (%s,%s,%s,'alerta_saldo')""",
                 [email, f"SisPorto – {s['cliente']}: saldo em {nivel}% ({s['descricao']})", corpo])
    from .integracoes import chat
    chat(f"{'⛔' if nivel == 100 else '⚠️'} *Saldo de horas {nivel}%* – {s['cliente']} · {s['descricao']}\n"
         f"{numeros.replace(chr(10), ' · ')}")
