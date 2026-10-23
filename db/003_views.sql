-- =============================================================================
-- SisPorto – Visões usadas pelos painéis (cliente, técnico e gerencial)
-- =============================================================================

-- Situação de SLA de cada chamado em aberto
CREATE OR REPLACE VIEW vw_chamados_sla AS
SELECT c.*,
       cl.nome_fantasia                                   AS cliente_nome,
       resp.nome                                          AS responsavel_nome,
       CASE
         WHEN c.status IN ('resolvido','fechado') THEN
           CASE WHEN c.prazo_solucao IS NULL OR c.resolvido_em <= c.prazo_solucao
                THEN 'cumprido' ELSE 'violado' END
         WHEN c.status = 'aguardando_cliente'  THEN 'pausado'
         WHEN c.prazo_solucao IS NULL          THEN 'sem_sla'
         WHEN now() > c.prazo_solucao          THEN 'estourado'
         WHEN fn_minutos_uteis_entre(now(), c.prazo_solucao)
              <= 0.2 * fn_minutos_uteis_entre(c.criado_em, c.prazo_solucao) THEN 'em_risco'
         ELSE 'no_prazo'
       END                                                AS situacao_sla,
       (c.primeira_resposta_em IS NULL AND c.prazo_1a_resposta IS NOT NULL
        AND now() > c.prazo_1a_resposta)                  AS resposta_atrasada,
       (SELECT coalesce(sum(a.minutos), 0) FROM apontamentos a
         WHERE a.chamado_id = c.id AND a.status <> 'rejeitado') AS minutos_apontados
  FROM chamados c
  JOIN clientes cl   ON cl.id = c.cliente_id
  LEFT JOIN usuarios resp ON resp.id = c.responsavel_id;

-- Horas por cliente / contrato / mês (base do painel do cliente e do faturamento)
CREATE OR REPLACE VIEW vw_horas_mes AS
SELECT ct.cliente_id,
       a.contrato_id,
       date_trunc('month', a.data)::date                                         AS mes,
       sum(a.minutos)        FILTER (WHERE a.status <> 'rejeitado')              AS minutos_total,
       sum(a.minutos)        FILTER (WHERE a.status = 'aprovado' AND a.faturavel) AS minutos_faturaveis_aprovados,
       sum(a.minutos)        FILTER (WHERE a.status = 'pendente')                AS minutos_pendentes,
       count(DISTINCT a.chamado_id)                                              AS chamados_atendidos
  FROM apontamentos a
  JOIN contratos ct ON ct.id = a.contrato_id
 GROUP BY ct.cliente_id, a.contrato_id, date_trunc('month', a.data);

-- Saldo dos bancos de horas (aprovadas + pendentes faturáveis consomem o saldo)
CREATE OR REPLACE VIEW vw_saldo_banco_horas AS
SELECT ct.id                                         AS contrato_id,
       ct.cliente_id,
       ct.descricao,
       ct.horas_pacote,
       ct.data_inicio,
       ct.data_fim                                   AS validade,
       round(coalesce(sum(a.minutos), 0) / 60.0, 2)  AS horas_consumidas,
       round(ct.horas_pacote - coalesce(sum(a.minutos), 0) / 60.0, 2) AS horas_saldo,
       round(100 * coalesce(sum(a.minutos), 0) / 60.0 / ct.horas_pacote, 1) AS pct_consumido
  FROM contratos ct
  LEFT JOIN apontamentos a
         ON a.contrato_id = ct.id AND a.faturavel AND a.status <> 'rejeitado'
 WHERE ct.tipo = 'banco_horas'
 GROUP BY ct.id;

-- Contrato mensal fixo: consumo da franquia no mês e horas excedentes
CREATE OR REPLACE VIEW vw_franquia_mes AS
SELECT ct.id                                              AS contrato_id,
       ct.cliente_id,
       h.mes,
       ct.franquia_horas,
       round(coalesce(h.minutos_faturaveis_aprovados, 0) / 60.0, 2) AS horas_aprovadas,
       greatest(round(coalesce(h.minutos_faturaveis_aprovados, 0) / 60.0, 2) - ct.franquia_horas, 0) AS horas_excedentes,
       round(greatest(coalesce(h.minutos_faturaveis_aprovados, 0) / 60.0 - ct.franquia_horas, 0)
             * coalesce(ct.valor_hora, 0), 2)              AS valor_excedente
  FROM contratos ct
  JOIN vw_horas_mes h ON h.contrato_id = ct.id
 WHERE ct.tipo = 'mensal_fixo' AND ct.franquia_horas IS NOT NULL;
