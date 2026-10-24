"""Regras de chamados compartilhadas entre a equipe e o portal do cliente."""
from __future__ import annotations

from flask import abort

from .db import db

PRIORIDADES = ["critica", "alta", "media", "baixa"]
STATUS = ["novo", "em_atendimento", "aguardando_cliente", "resolvido", "fechado"]
ABERTOS = ("novo", "em_atendimento", "aguardando_cliente")


def buscar(numero: int, cliente_id: int | None = None):
    """Chamado com situação de SLA. Com cliente_id, garante que pertence ao cliente."""
    sql = "SELECT * FROM vw_chamados_sla WHERE numero = %s"
    params = [numero]
    if cliente_id is not None:
        sql += " AND cliente_id = %s"
        params.append(cliente_id)
    ch = db().one(sql, params)
    if not ch:
        abort(404)
    return ch


def criar(*, cliente_id, titulo, descricao, prioridade, origem, solicitante_id=None,
          solicitante_email=None, contrato_id=None, categoria=None, responsavel_id=None):
    titulo, descricao = (titulo or "").strip(), (descricao or "").strip()
    if not titulo or not descricao:
        raise ValueError("Informe título e descrição.")
    if prioridade not in PRIORIDADES:
        raise ValueError("Prioridade inválida.")
    if contrato_id:
        ok = db().val("SELECT 1 FROM contratos WHERE id = %s AND cliente_id = %s", [contrato_id, cliente_id])
        if not ok:
            raise ValueError("Contrato não pertence ao cliente.")
    novo = db().one(
        """INSERT INTO chamados (cliente_id, contrato_id, solicitante_id, solicitante_email, responsavel_id,
                                titulo, descricao, categoria, prioridade, origem)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, numero""",
        [cliente_id, contrato_id or None, solicitante_id, solicitante_email, responsavel_id or None,
         titulo, descricao, (categoria or "").strip() or None, prioridade, origem])
    if prioridade == "critica":
        from .integracoes import alerta_critico
        alerta_critico(novo["id"])
    return novo


def responder(chamado_id, autor_id, texto, interna=False):
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Escreva a mensagem.")
    return db().val("""INSERT INTO chamado_interacoes (chamado_id, autor_id, tipo, texto)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    [chamado_id, autor_id, "interna" if interna else "publica", texto])


def linha_do_tempo(chamado_id, incluir_internas=True):
    """Mensagens + mudanças de status em ordem cronológica."""
    filtro = "" if incluir_internas else "AND i.tipo = 'publica'"
    msgs = db().all(f"""
        SELECT 'msg' AS tipo_item, i.criado_em AS em, i.tipo, i.texto,
               coalesce(u.nome, i.autor_email, 'Sistema') AS autor, u.perfil
          FROM chamado_interacoes i LEFT JOIN usuarios u ON u.id = i.autor_id
         WHERE i.chamado_id = %s {filtro}""", [chamado_id])
    hist = db().all("""
        SELECT 'status' AS tipo_item, h.em, h.de::text AS de, h.para::text AS para,
               coalesce(u.nome, 'Sistema') AS autor
          FROM chamado_status_historico h LEFT JOIN usuarios u ON u.id = h.usuario_id
         WHERE h.chamado_id = %s AND h.de IS NOT NULL""", [chamado_id])
    return sorted(msgs + hist, key=lambda x: x["em"])
