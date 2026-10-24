-- =============================================================================
-- SisPorto – Migração 025: cadastros mais completos, custo/hora, NPS, agenda,
-- Google Drive, alertas do Chat e 2FA
-- =============================================================================

-- Clientes ----------------------------------------------------------------------
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS inscricao_estadual   text;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS inscricao_municipal  text;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS segmento             text;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS site                 text;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cep                  varchar(8);
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cidade               text;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS uf                   varchar(2);
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS responsavel_id       bigint REFERENCES usuarios(id);
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cliente_desde        date;

-- Usuários (recursos e usuários de clientes) ------------------------------------
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS cargo         text;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS departamento  text;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS custo_hora    numeric(10,2) CHECK (custo_hora >= 0);  -- recursos
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS recebe_nps    boolean NOT NULL DEFAULT false;         -- clientes
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS observacoes   text;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS totp_segredo  text;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS totp_ativo    boolean NOT NULL DEFAULT false;

-- Contratos -----------------------------------------------------------------------
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS observacoes text;

-- Chamados: alertas enviados ao Chat e pasta no Drive -------------------------------
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS alerta_risco_em    timestamptz;
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS alerta_estouro_em  timestamptz;
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS drive_pasta_url    text;

-- Anexos: cópia no Google Drive -------------------------------------------------------
ALTER TABLE chamado_anexos ADD COLUMN IF NOT EXISTS drive_arquivo_id text;
ALTER TABLE chamado_anexos ADD COLUMN IF NOT EXISTS drive_url        text;

-- Agenda: visitas e reuniões ligadas a chamados ---------------------------------------
CREATE TABLE IF NOT EXISTS agendamentos (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id       bigint      NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  recurso_id       bigint      NOT NULL REFERENCES usuarios(id),
  tipo             text        NOT NULL CHECK (tipo IN ('visita', 'reuniao')),
  inicio           timestamptz NOT NULL,
  fim              timestamptz NOT NULL CHECK (fim > inicio),
  local            text,
  descricao        text,
  convidados       text        NOT NULL,          -- e-mails separados por vírgula
  status           text        NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente','criado','cancelar','cancelado')),
  google_evento_id text,
  criado_por       bigint      REFERENCES usuarios(id),
  criado_em        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agendamentos_pendentes ON agendamentos (status) WHERE status IN ('pendente','cancelar');

-- NPS mensal ------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nps_respostas (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id    bigint      NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  cliente_id    bigint      NOT NULL REFERENCES clientes(id),
  mes           date        NOT NULL,                       -- 1º dia do mês da pesquisa
  token_hash    text        NOT NULL UNIQUE,
  enviado_em    timestamptz NOT NULL DEFAULT now(),
  respondido_em timestamptz,
  nota          smallint    CHECK (nota BETWEEN 0 AND 10),
  comentario    text,
  UNIQUE (usuario_id, mes)
);

-- Visão de BI (Looker Studio via Google Planilhas) --------------------------------------
CREATE OR REPLACE VIEW vw_bi_apontamentos AS
SELECT a.id,
       a.data,
       date_trunc('month', a.data)::date                         AS mes,
       coalesce(cl.nome_fantasia, cl.razao_social)               AS cliente,
       ct.descricao                                              AS contrato,
       ct.tipo::text                                             AS tipo_contrato,
       c.numero                                                  AS chamado,
       coalesce(ts.nome, 'Sem tipo')                             AS tipo_servico,
       u.nome                                                    AS recurso,
       a.status::text                                            AS status,
       a.faturavel,
       round(a.minutos / 60.0, 2)                                AS horas,
       ct.valor_hora                                             AS valor_hora_venda,
       u.custo_hora                                              AS custo_hora_recurso,
       round(CASE WHEN a.faturavel THEN a.minutos / 60.0 * coalesce(ct.valor_hora, 0) ELSE 0 END, 2) AS receita_horas,
       round(a.minutos / 60.0 * coalesce(u.custo_hora, 0), 2)    AS custo
  FROM apontamentos a
  JOIN usuarios u ON u.id = a.usuario_id
  LEFT JOIN chamados c ON c.id = a.chamado_id
  LEFT JOIN contratos ct ON ct.id = coalesce(a.contrato_id, c.contrato_id)
  LEFT JOIN clientes cl ON cl.id = coalesce(c.cliente_id, ct.cliente_id)
  LEFT JOIN tipos_servico ts ON ts.id = a.tipo_servico_id
 WHERE a.status <> 'rejeitado';

-- Recria a visão de SLA para incluir as colunas novas de chamados (c.* é fixado na criação)
DROP VIEW IF EXISTS vw_chamados_sla;
CREATE VIEW vw_chamados_sla AS
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
