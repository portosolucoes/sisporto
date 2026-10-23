"""Dados de exemplo para testes e demonstração (nunca rodar em produção)."""
from __future__ import annotations

import datetime as dt

from werkzeug.security import generate_password_hash

from .db import db
from .util import TZ

SENHA_DEMO = "demo-sisporto-2026"


def carregar():
    d = db()
    hoje = dt.datetime.now(TZ).date()
    mes = hoje.replace(day=1)
    h = generate_password_hash(SENHA_DEMO)

    d.run("UPDATE usuarios SET senha_hash = %s WHERE email = 'contato@portosolucoes.com.br'", [h])
    tec = d.val("""INSERT INTO usuarios (nome, email, perfil, senha_hash)
                   VALUES ('Ana Técnica', 'ana@portosolucoes.com.br', 'tecnico', %s) RETURNING id""", [h])
    adm = d.val("SELECT id FROM usuarios WHERE email = 'contato@portosolucoes.com.br'")

    alfa = d.val("""INSERT INTO clientes (razao_social, nome_fantasia, cnpj, email_financeiro, dominio_email)
                    VALUES ('Alfa Logística Ltda', 'Alfa Logística', '11222333000181',
                            'financeiro@alfalog.com.br', 'alfalog.com.br') RETURNING id""")
    beta = d.val("""INSERT INTO clientes (razao_social, nome_fantasia, cnpj)
                    VALUES ('Beta Clínica Médica S/A', 'Clínica Beta', '45723174000110') RETURNING id""")

    ct_alfa = d.val("""INSERT INTO contratos (cliente_id, tipo, descricao, valor_mensal, franquia_horas, valor_hora,
                                              data_inicio, dia_vencimento, indice_reajuste, mes_reajuste)
                       VALUES (%s, 'mensal_fixo', 'Suporte mensal – 10h', 2500, 10, 180, '2026-01-01', 10, 'IPCA', 1)
                       RETURNING id""", [alfa])
    ct_beta = d.val("""INSERT INTO contratos (cliente_id, tipo, descricao, horas_pacote, valor_hora,
                                              data_inicio, data_fim)
                       VALUES (%s, 'banco_horas', 'Pacote 40h', 40, 160, '2026-07-01', '2027-06-30')
                       RETURNING id""", [beta])

    maria = d.val("""INSERT INTO usuarios (nome, email, perfil, cliente_id, senha_hash)
                     VALUES ('Maria Souza', 'maria@alfalog.com.br', 'cliente', %s, %s) RETURNING id""", [alfa, h])
    joao = d.val("""INSERT INTO usuarios (nome, email, perfil, cliente_id, senha_hash)
                    VALUES ('João Lima', 'joao@clinicabeta.com.br', 'cliente', %s, %s) RETURNING id""", [beta, h])

    def chamado(cli, sol, titulo, desc, prio, dias_atras, resp=None, status=None):
        criado = dt.datetime.now(TZ) - dt.timedelta(days=dias_atras, hours=2)
        r = d.one("""INSERT INTO chamados (cliente_id, solicitante_id, responsavel_id, titulo, descricao,
                                           prioridade, origem, criado_em)
                     VALUES (%s,%s,%s,%s,%s,%s,'portal',%s) RETURNING id, numero""",
                  [cli, sol, resp, titulo, desc, prio, criado])
        if status:
            d.run("UPDATE chamados SET status = %s WHERE id = %s", [status, r["id"]])
        return r["id"]

    c1 = chamado(alfa, maria, "Impressora do faturamento não imprime", "Fila travada desde cedo.", "alta", 0, tec)
    d.run("INSERT INTO chamado_interacoes (chamado_id, autor_id, texto) VALUES (%s,%s,%s)",
          [c1, tec, "Olá Maria, já estou acessando remotamente para verificar a fila."])
    c2 = chamado(alfa, maria, "Novo usuário para o RH", "Criar e-mail e acesso ao ERP para Carla.", "media", 3, tec)
    d.run("INSERT INTO chamado_interacoes (chamado_id, autor_id, texto) VALUES (%s,%s,%s)",
          [c2, tec, "Precisamos do CPF e do cargo da Carla para liberar o ERP."])
    d.run("UPDATE chamados SET status = 'aguardando_cliente' WHERE id = %s", [c2])
    c3 = chamado(beta, joao, "Lentidão no sistema de prontuário", "Muito lento desde segunda.", "critica", 1)
    c4 = chamado(beta, joao, "Backup do servidor", "Conferir se o backup noturno está rodando.", "baixa", 12, tec,
                 "resolvido")
    chamado(alfa, maria, "Troca de toner", "Toner da impressora da recepção.", "baixa", 20, tec, "fechado")

    def horas(cham, ct, dias_atras, minutos, desc, status="aprovado", fat=True):
        d.run("""INSERT INTO apontamentos (usuario_id, chamado_id, contrato_id, data, minutos, faturavel, descricao,
                                           status, aprovado_por)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              [tec, cham, ct, hoje - dt.timedelta(days=dias_atras), minutos, fat, desc, status,
               adm if status != "pendente" else None])

    horas(c1, ct_alfa, 0, 45, "Acesso remoto e limpeza da fila de impressão", "pendente")
    horas(c2, ct_alfa, 3, 30, "Levantamento dos acessos necessários")
    horas(c4, ct_beta, 12, 120, "Revisão do agendamento e teste de restauração")
    horas(c4, ct_beta, 11, 60, "Ajuste da retenção dos backups")
    horas(c3, ct_beta, 1, 90, "Análise de desempenho do servidor", "pendente")
    # histórico dos meses anteriores (para o gráfico do portal)
    for m, minutos in ((1, 540), (2, 720), (3, 380), (4, 660)):
        data_ref = (mes - dt.timedelta(days=28 * m)).replace(day=15)
        if data_ref >= dt.date(2026, 1, 1):
            d.run("""INSERT INTO apontamentos (usuario_id, chamado_id, contrato_id, data, minutos, descricao, status,
                                               aprovado_por)
                     VALUES (%s,%s,%s,%s,%s,'Atendimentos do mês','aprovado',%s)""",
                  [tec, c2, ct_alfa, data_ref, minutos, adm])
    return {"alfa": alfa, "beta": beta, "ct_alfa": ct_alfa, "ct_beta": ct_beta, "maria": maria, "joao": joao,
            "tec": tec, "adm": adm}
