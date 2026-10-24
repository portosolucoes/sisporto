"""Saldos de horas: contrato (horas liberadas) rateado entre chamados, cronômetro e regras de atendimento.

Regras:
- Chamado com horas alocadas consome só o que foi alocado a ele.
- Chamado sem alocação usa o "saldo livre" do contrato: horas liberadas − consumido − o que ainda falta
  usar nas alocações dos outros chamados − o que os cronômetros abertos (de qualquer recurso, em qualquer
  chamado sem alocação do mesmo contrato) já contaram.
- No mensal fixo as horas liberadas valem por mês (vw_saldo_banco_horas).
- Para atender (lançar horas, cronômetro, responder, mudar status) o chamado precisa de contrato ativo,
  vigente e com saldo positivo (ou sem limite de horas), e de responsável.
"""
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


def _filtro_mes(contrato_tipo, alias="a"):
    return f" AND {alias}.data >= date_trunc('month', current_date)::date" if contrato_tipo == "mensal_fixo" else ""


def _reservado_outros(contrato_id, chamado_id) -> int:
    """Minutos alocados a outros chamados do contrato que ainda não foram apontados."""
    return int(db().val("""
        SELECT coalesce(sum(greatest(c.horas_alocadas * 60 - coalesce(
                 (SELECT sum(a.minutos) FROM apontamentos a WHERE a.chamado_id = c.id
                     AND a.status <> 'rejeitado' AND a.faturavel), 0), 0)), 0)
          FROM chamados c WHERE c.contrato_id = %s AND c.id <> %s AND c.horas_alocadas IS NOT NULL""",
        [contrato_id, chamado_id or 0]))


def _info(chamado_id):
    return db().one("""SELECT c.id, c.horas_alocadas, c.contrato_id, ct.horas_pacote, ct.descricao AS contrato,
                              ct.tipo::text AS contrato_tipo
                         FROM chamados c LEFT JOIN contratos ct ON ct.id = c.contrato_id WHERE c.id = %s""",
                    [chamado_id])


def saldo_chamado(chamado_id) -> dict:
    """Saldo em minutos para lançar no chamado (sem contar cronômetros abertos)."""
    c = _info(chamado_id)
    usado = db().val("""SELECT coalesce(sum(minutos), 0) FROM apontamentos
                         WHERE chamado_id = %s AND status <> 'rejeitado'""", [chamado_id])
    if c["horas_alocadas"] is not None:
        limite = int(c["horas_alocadas"] * 60)
        return {"origem": "chamado", "limite": limite, "usado": usado, "restante": limite - usado,
                "descricao": f"{horas_dec(c['horas_alocadas'])} alocadas a este chamado"}
    if c["horas_pacote"] is not None:
        s = db().one("SELECT horas_saldo FROM vw_saldo_banco_horas WHERE contrato_id = %s", [c["contrato_id"]])
        restante = int(s["horas_saldo"] * 60 - _reservado_outros(c["contrato_id"], chamado_id)) if s else None
        mensal = " do mês" if c["contrato_tipo"] == "mensal_fixo" else ""
        return {"origem": "contrato", "limite": int(c["horas_pacote"] * 60), "usado": usado, "restante": restante,
                "descricao": f"saldo{mensal} do contrato {c['contrato']}"}
    return {"origem": None, "limite": None, "usado": usado, "restante": None, "descricao": "sem limite de horas"}


def _cronometros_concorrentes(chamado_id, usuario_id):
    """Cronômetros que disputam o mesmo saldo (exceto o do próprio usuário neste chamado)."""
    c = _info(chamado_id)
    if c["horas_alocadas"] is not None:
        return db().all("SELECT * FROM cronometros WHERE chamado_id = %s AND usuario_id <> %s",
                        [chamado_id, usuario_id])
    if c["horas_pacote"] is not None:
        return db().all("""SELECT k.* FROM cronometros k JOIN chamados x ON x.id = k.chamado_id
                            WHERE x.contrato_id = %s AND x.horas_alocadas IS NULL
                              AND NOT (k.chamado_id = %s AND k.usuario_id = %s)""",
                        [c["contrato_id"], chamado_id, usuario_id])
    return []


def restante_para_cronometro(chamado_id, usuario_id) -> int | None:
    """Segundos que o cronômetro deste usuário ainda pode correr (None = sem limite)."""
    return estado_saldo_cronometro(chamado_id, usuario_id)["limite"]


def estado_saldo_cronometro(chamado_id, usuario_id) -> dict:
    """limite: segundos disponíveis para o meu cronômetro agora; outros_rodando: quantos cronômetros
    concorrentes estão correndo (a tela desconta o tempo deles em tempo real)."""
    s = saldo_chamado(chamado_id)
    if s["restante"] is None:
        return {"limite": None, "outros_rodando": 0}
    outros = _cronometros_concorrentes(chamado_id, usuario_id)
    usados = sum(segundos_cronometro(k) for k in outros)
    return {"limite": max(s["restante"] * 60 - usados, 0),
            "outros_rodando": sum(1 for k in outros if k["rodando_desde"])}


def minutos_de(segundos: int) -> int:
    return max(1, math.ceil(segundos / 60))


def validar_alocacao(contrato_id, chamado_id, horas) -> None:
    """Impede alocar aos chamados mais horas do que o contrato liberou."""
    ct = db().one("""SELECT coalesce(s.horas_liberadas, c.horas_pacote) AS horas_pacote, c.descricao, c.tipo::text AS tipo
                       FROM contratos c LEFT JOIN vw_saldo_banco_horas s ON s.contrato_id = c.id
                      WHERE c.id = %s""", [contrato_id])
    if not ct or ct["horas_pacote"] is None:
        raise ValueError("O contrato deste chamado não tem horas liberadas para ratear.")
    usado = db().val("SELECT coalesce(sum(minutos),0) FROM apontamentos WHERE chamado_id = %s AND status <> 'rejeitado'",
                     [chamado_id])
    if horas * 60 < usado:
        raise ValueError(f"Este chamado já tem {usado // 60}h{usado % 60:02d} apontadas; aloque pelo menos isso.")
    consumido_fora = db().val(f"""SELECT coalesce(sum(minutos), 0) FROM apontamentos a
                                   WHERE a.contrato_id = %s AND a.faturavel AND a.status <> 'rejeitado'
                                     AND a.chamado_id IS DISTINCT FROM %s {_filtro_mes(ct['tipo'])}""",
                              [contrato_id, chamado_id])
    comprometido = _reservado_outros(contrato_id, chamado_id) + consumido_fora
    livre = int(ct["horas_pacote"] * 60) - comprometido
    if horas * 60 > livre:
        raise ValueError(f"O contrato {ct['descricao']} tem {horas_dec(ct['horas_pacote'])} liberadas e "
                         f"{comprometido // 60}h{comprometido % 60:02d} já usadas ou reservadas a outros chamados. "
                         f"Disponível para alocar: {max(livre, 0) // 60}h{max(livre, 0) % 60:02d}.")


# --- regras de atendimento ---------------------------------------------------------------
def situacao_contrato(contrato_id) -> dict:
    """{'ok': bool, 'motivo': str, 'saldo_min': int|None}"""
    if not contrato_id:
        return {"ok": False, "motivo": "O chamado não tem contrato vinculado.", "saldo_min": None}
    ct = db().one("""SELECT ct.ativo, ct.data_inicio, ct.data_fim, ct.horas_pacote, ct.descricao, s.horas_saldo
                       FROM contratos ct LEFT JOIN vw_saldo_banco_horas s ON s.contrato_id = ct.id
                      WHERE ct.id = %s""", [contrato_id])
    hoje = dt.datetime.now(TZ).date()
    if not ct:
        return {"ok": False, "motivo": "Contrato não encontrado.", "saldo_min": None}
    if not ct["ativo"]:
        return {"ok": False, "motivo": f"O contrato {ct['descricao']} está inativo.", "saldo_min": None}
    if ct["data_inicio"] and ct["data_inicio"] > hoje:
        return {"ok": False, "motivo": f"O contrato {ct['descricao']} só começa em {ct['data_inicio']:%d/%m/%Y}.",
                "saldo_min": None}
    if ct["data_fim"] and ct["data_fim"] < hoje:
        return {"ok": False, "motivo": f"O contrato {ct['descricao']} venceu em {ct['data_fim']:%d/%m/%Y}.",
                "saldo_min": None}
    if ct["horas_pacote"] is not None:
        saldo = int((ct["horas_saldo"] or 0) * 60)
        if saldo <= 0:
            return {"ok": False, "motivo": f"O contrato {ct['descricao']} está sem saldo de horas.", "saldo_min": saldo}
        return {"ok": True, "motivo": "", "saldo_min": saldo}
    return {"ok": True, "motivo": "", "saldo_min": None}


def pendencias(c) -> list[str]:
    """O que falta no chamado para poder atender/apontar."""
    p = []
    if not c.get("contrato_id"):
        p.append("contrato")
    if not c.get("responsavel_id"):
        p.append("responsável")
    if not c.get("status"):
        p.append("status")
    if not c.get("prioridade"):
        p.append("prioridade")
    return p


def bloqueio_atendimento(c, exigir_saldo=True) -> str | None:
    """Mensagem de bloqueio, ou None se o chamado pode ser atendido/apontado."""
    falta = pendencias(c)
    if falta:
        return "Preencha " + ", ".join(falta) + " do chamado (caixa Atualizar) antes de atender ou lançar horas."
    s = situacao_contrato(c["contrato_id"])
    if not s["ok"] and (exigir_saldo or "saldo" not in s["motivo"]):
        return s["motivo"] + " Vincule um contrato ativo com saldo de horas (ou peça ao administrador)."
    return None


def tipos_permitidos(contrato_id):
    """Tipos de serviço aceitos nos apontamentos: os itens do contrato, ou todos os ativos se não houver itens."""
    itens = db().all("""SELECT ts.id, ts.nome FROM contrato_itens i JOIN tipos_servico ts ON ts.id = i.tipo_servico_id
                         WHERE i.contrato_id = %s ORDER BY i.ordem, i.id""", [contrato_id]) if contrato_id else []
    return itens or db().all("SELECT id, nome FROM tipos_servico WHERE ativo ORDER BY ordem, nome")


def validar_tipo(contrato_id, tipo_id):
    if not tipo_id or tipo_id not in {t["id"] for t in tipos_permitidos(contrato_id)}:
        raise ValueError("Tipo de serviço não previsto no contrato deste chamado.")


# --- fechamento de período ---------------------------------------------------------------------
def periodo_fechado(data) -> dict | None:
    if not data:
        return None
    return db().one("SELECT * FROM periodos_fechados WHERE %s BETWEEN inicio AND fim ORDER BY id LIMIT 1", [data])


def checar_periodo(*datas):
    for d in datas:
        p = periodo_fechado(d)
        if p:
            raise ValueError(f"O período de {p['inicio']:%d/%m/%Y} a {p['fim']:%d/%m/%Y} está fechado: "
                             "as horas não podem ser lançadas, alteradas ou excluídas. Fale com o administrador.")
