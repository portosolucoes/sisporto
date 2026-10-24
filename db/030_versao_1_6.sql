-- 030 (versão 1.6): avisos de saldo, horas extras aprovadas pelo cliente, respostas prontas,
-- base de conhecimento e fechamento de período do timesheet

-- 1. avisos de saldo (80% e 100%), um por nível e período ----------------------------------------
CREATE TABLE IF NOT EXISTS saldo_alertas (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  contrato_id  bigint NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
  periodo      date NOT NULL,              -- 1º dia do mês (mensal fixo) ou data de início do contrato
  nivel        integer NOT NULL CHECK (nivel IN (80, 100)),
  pct          numeric(6,1),
  enviado_em   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (contrato_id, periodo, nivel)
);
COMMENT ON TABLE saldo_alertas IS 'Avisos de consumo de saldo (80% e 100%) já enviados, um por nível e período.';
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS recebe_alerta_saldo boolean NOT NULL DEFAULT false;
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS aprova_horas_extras boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN usuarios.recebe_alerta_saldo IS 'Usuário do cliente que recebe os avisos de 80%/100% do saldo de horas.';
COMMENT ON COLUMN usuarios.aprova_horas_extras IS 'Usuário do cliente que pode aprovar horas extras no portal.';

-- 2. horas extras (pedido da Porto, aprovação do cliente no portal) --------------------------------
CREATE TABLE IF NOT EXISTS horas_extras (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  contrato_id     bigint NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
  chamado_id      bigint REFERENCES chamados(id) ON DELETE SET NULL,
  horas           numeric(9,2) NOT NULL CHECK (horas > 0),
  valor_hora      numeric(12,2) CHECK (valor_hora IS NULL OR valor_hora >= 0),
  justificativa   text NOT NULL,
  mes             date NOT NULL DEFAULT date_trunc('month', current_date)::date,
  status          text NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'aprovado', 'recusado', 'cancelado')),
  solicitado_por  bigint NOT NULL REFERENCES usuarios(id),
  solicitado_em   timestamptz NOT NULL DEFAULT now(),
  respondido_por  bigint REFERENCES usuarios(id),
  respondido_em   timestamptz,
  resposta        text
);
CREATE INDEX IF NOT EXISTS horas_extras_contrato ON horas_extras (contrato_id, status);
COMMENT ON TABLE horas_extras IS 'Pedidos de horas extras: a Porto solicita, o cliente aprova no portal; aprovadas somam ao saldo (no mensal fixo, só no mês do pedido).';

-- 3. respostas prontas e base de conhecimento --------------------------------------------------------
CREATE TABLE IF NOT EXISTS respostas_prontas (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  titulo     text NOT NULL UNIQUE,
  texto      text NOT NULL,
  ativo      boolean NOT NULL DEFAULT true,
  criado_em  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE respostas_prontas IS 'Modelos de resposta para os chamados; aceitam {cliente}, {solicitante}, {numero}, {titulo} e {recurso}.';

CREATE TABLE IF NOT EXISTS artigos (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  titulo         text NOT NULL,
  conteudo       text NOT NULL,
  categoria      text,
  palavras       text,
  publico        boolean NOT NULL DEFAULT false,
  ativo          boolean NOT NULL DEFAULT true,
  visualizacoes  integer NOT NULL DEFAULT 0,
  util_sim       integer NOT NULL DEFAULT 0,
  util_nao       integer NOT NULL DEFAULT 0,
  criado_por     bigint REFERENCES usuarios(id),
  criado_em      timestamptz NOT NULL DEFAULT now(),
  atualizado_em  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE artigos IS 'Base de conhecimento: artigos internos (equipe) ou públicos (clientes veem na Ajuda do portal).';

-- 4. fechamento de período do timesheet ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS periodos_fechados (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  inicio       date NOT NULL,
  fim          date NOT NULL CHECK (fim >= inicio),
  observacao   text,
  fechado_por  bigint REFERENCES usuarios(id),
  fechado_em   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE periodos_fechados IS 'Períodos de horas fechados: apontamentos com data dentro deles não podem ser criados, alterados ou excluídos.';

CREATE OR REPLACE FUNCTION tg_apontamento_periodo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND EXISTS (SELECT 1 FROM periodos_fechados p WHERE OLD.data BETWEEN p.inicio AND p.fim) THEN
    RAISE EXCEPTION 'PERIODO_FECHADO: o período de % está fechado', to_char(OLD.data, 'DD/MM/YYYY') USING ERRCODE = 'P0001';
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') AND EXISTS (SELECT 1 FROM periodos_fechados p WHERE NEW.data BETWEEN p.inicio AND p.fim) THEN
    RAISE EXCEPTION 'PERIODO_FECHADO: o período de % está fechado', to_char(NEW.data, 'DD/MM/YYYY') USING ERRCODE = 'P0001';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
DROP TRIGGER IF EXISTS apontamentos_periodo ON apontamentos;
CREATE TRIGGER apontamentos_periodo BEFORE INSERT OR UPDATE OR DELETE ON apontamentos
  FOR EACH ROW EXECUTE FUNCTION tg_apontamento_periodo();

-- 5. saldo considera as horas extras aprovadas ----------------------------------------------------------
CREATE OR REPLACE VIEW vw_saldo_banco_horas AS
WITH extras AS (
  SELECT ct.id AS contrato_id, coalesce(sum(e.horas), 0) AS horas
    FROM contratos ct LEFT JOIN horas_extras e
      ON e.contrato_id = ct.id AND e.status = 'aprovado'
     AND (ct.tipo <> 'mensal_fixo' OR e.mes = date_trunc('month', current_date)::date)
   GROUP BY ct.id)
SELECT ct.id                                         AS contrato_id,
       ct.cliente_id,
       ct.descricao,
       ct.horas_pacote,
       ct.data_inicio,
       ct.data_fim                                   AS validade,
       round(coalesce(sum(a.minutos), 0) / 60.0, 2)  AS horas_consumidas,
       round(ct.horas_pacote + x.horas - coalesce(sum(a.minutos), 0) / 60.0, 2) AS horas_saldo,
       round(100 * coalesce(sum(a.minutos), 0) / 60.0 / nullif(ct.horas_pacote + x.horas, 0), 1) AS pct_consumido,
       CASE WHEN ct.tipo = 'mensal_fixo' THEN 'mensal' ELSE 'total' END AS periodo,
       x.horas                                       AS horas_extras,
       ct.horas_pacote + x.horas                     AS horas_liberadas
  FROM contratos ct
  JOIN extras x ON x.contrato_id = ct.id
  LEFT JOIN apontamentos a
         ON a.contrato_id = ct.id AND a.faturavel AND a.status <> 'rejeitado'
        AND (ct.tipo <> 'mensal_fixo' OR a.data >= date_trunc('month', current_date)::date)
 WHERE ct.horas_pacote IS NOT NULL
 GROUP BY ct.id, x.horas;
COMMENT ON VIEW vw_saldo_banco_horas IS 'Saldo de horas (liberadas + extras aprovadas − consumo). Mensal fixo: mês corrente.';
