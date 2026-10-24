"""Saldos de horas: projeto/contrato (horas liberadas) rateado entre chamados, e cronômetro."""
from __future__ import annotations

import datetime as dt
import math

from .db import db
from .util import TZ, horas_dec


def segundos_cronometro(c) -> int:
    total = c["segundos_acumulados"]
    if c["rodando_desde"]:
        total += int((dt.datetime.now(TZ) - c["rodando_desde"]).total_seconds())
    return max(total, 0)


def saldo_chamado(chamado_id) -> dict:
    """Saldo em minutos disponível para lançar no chamado.

    - Chamado com horas alocadas: alocadas − apontado no chamado.
    - Senão, contrato com horas liberadas: saldo do contrato.
    - Senão: sem limite (restante = None).
    Cronômetros rodando/pausados de outros recursos no mesmo chamado também consomem o saldo.
    """
    c = db().one("""SELECT c.id, c.horas_alocadas, c.contrato_id, ct.horas_pacote, ct.descricao AS contrato
                      FROM chamados c LEFT JOIN contratos ct ON ct.id = c.contrato_id WHERE c.id = %s""", [chamado_id])
    usado = db().val("""SELECT coalesce(sum(minutos), 0) FROM apontamentos
                         WHERE chamado_id = %s AND status <> 'rejeitado'""", [chamado_id])
    if c["horas_alocadas"] is not None:
        limite = int(c["horas_alocadas"] * 60)
        return {"origem": "chamado", "limite": limite, "usado": usado, "restante": limite - usado,
                "descricao": f"{horas_dec(c['horas_alocadas'])} alocadas a este chamado"}
    if c["horas_pacote"] is not None:
        s = db().one("SELECT horas_pacote, horas_consumidas, horas_saldo FROM vw_saldo_banco_horas WHERE contrato_id = %s",
                     [c["contrato_id"]])
        # horas reservadas a outros chamados e ainda não consumidas não podem ser usadas aqui
        reservado = _reservado_outros(c["contrato_id"], chamado_id)
        restante = int(s["horas_saldo"] * 60 - reservado) if s else None
        return {"origem": "contrato", "limite": int(c["horas_pacote"] * 60), "usado": usado, "restante": restante,
                "descricao": f"saldo do contrato {c['contrato']}"}
    return {"origem": None, "limite": None, "usado": usado, "restante": None, "descricao": "sem limite de horas"}


def _reservado_outros(contrato_id, chamado_id) -> int:
    """Minutos alocados a outros chamados do contrato que ainda não foram apontados."""
    return int(db().val("""
        SELECT coalesce(sum(greatest(c.horas_alocadas * 60 - coalesce(
                 (SELECT sum(a.minutos) FROM apontamentos a WHERE a.chamado_id = c.id
                     AND a.status <> 'rejeitado' AND a.faturavel), 0), 0)), 0)
          FROM chamados c WHERE c.contrato_id = %s AND c.id <> %s AND c.horas_alocadas IS NOT NULL""",
        [contrato_id, chamado_id or 0]))


def restante_para_cronometro(chamado_id, usuario_id) -> int | None:
    """Segundos que o cronômetro deste usuário ainda pode correr (None = sem limite)."""
    s = saldo_chamado(chamado_id)
    if s["restante"] is None:
        return None
    outros = sum(segundos_cronometro(c) for c in db().all(
        "SELECT * FROM cronometros WHERE chamado_id = %s AND usuario_id <> %s", [chamado_id, usuario_id]))
    return max(s["restante"] * 60 - outros, 0)


def minutos_de(segundos: int) -> int:
    return max(1, math.ceil(segundos / 60))


def validar_alocacao(contrato_id, chamado_id, horas) -> None:
    """Impede alocar aos chamados mais horas do que o contrato liberou.

    Conta: esta alocação + o que falta usar nas alocações dos outros chamados + tudo que já foi
    consumido no contrato fora deste chamado  <=  horas liberadas.
    """
    ct = db().one("SELECT horas_pacote, descricao FROM contratos WHERE id = %s", [contrato_id])
    if not ct or ct["horas_pacote"] is None:
        raise ValueError("O contrato deste chamado não tem horas liberadas para ratear.")
    usado = db().val("SELECT coalesce(sum(minutos),0) FROM apontamentos WHERE chamado_id = %s AND status <> 'rejeitado'",
                     [chamado_id])
    if horas * 60 < usado:
        raise ValueError(f"Este chamado já tem {usado // 60}h{usado % 60:02d} apontadas; aloque pelo menos isso.")
    consumido_fora = db().val("""SELECT coalesce(sum(minutos), 0) FROM apontamentos
                                  WHERE contrato_id = %s AND faturavel AND status <> 'rejeitado'
                                    AND chamado_id IS DISTINCT FROM %s""", [contrato_id, chamado_id])
    comprometido = _reservado_outros(contrato_id, chamado_id) + consumido_fora
    livre = int(ct["horas_pacote"] * 60) - comprometido
    if horas * 60 > livre:
        raise ValueError(f"O contrato {ct['descricao']} tem {horas_dec(ct['horas_pacote'])} liberadas e "
                         f"{comprometido // 60}h{comprometido % 60:02d} já usadas ou reservadas a outros chamados. "
                         f"Disponível para alocar: {max(livre, 0) // 60}h{max(livre, 0) % 60:02d}.")
