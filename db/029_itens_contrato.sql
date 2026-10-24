-- 029: itens do contrato (vários tipos de serviço por contrato) e saldo mensal no mensal fixo

CREATE TABLE IF NOT EXISTS contrato_itens (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  contrato_id      bigint NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
  tipo_servico_id  bigint NOT NULL REFERENCES tipos_servico(id),
  horas            numeric(9,2) CHECK (horas IS NULL OR horas >= 0),
  valor_hora       numeric(12,2) CHECK (valor_hora IS NULL OR valor_hora >= 0),
  descricao        text,
  ordem            integer NOT NULL DEFAULT 0,
  UNIQUE (contrato_id, tipo_servico_id)
);
CREATE INDEX IF NOT EXISTS contrato_itens_contrato ON contrato_itens (contrato_id);
COMMENT ON TABLE contrato_itens IS 'Itens do contrato: tipos de serviço cobertos, com horas e valor da hora próprios (opcionais).';
COMMENT ON COLUMN contrato_itens.horas IS 'Horas liberadas para este tipo de serviço (a soma vira as horas liberadas do contrato).';
COMMENT ON COLUMN contrato_itens.valor_hora IS 'Valor da hora deste tipo de serviço; vazio = valor hora do contrato.';

-- contratos que já tinham um tipo de serviço viram um item
INSERT INTO contrato_itens (contrato_id, tipo_servico_id, horas)
SELECT id, tipo_servico_id, horas_pacote FROM contratos WHERE tipo_servico_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- validações de contrato
ALTER TABLE contratos DROP CONSTRAINT IF EXISTS ck_contrato_vigencia;
ALTER TABLE contratos ADD CONSTRAINT ck_contrato_vigencia CHECK (data_fim IS NULL OR data_fim >= data_inicio) NOT VALID;
ALTER TABLE contratos DROP CONSTRAINT IF EXISTS ck_contrato_dia_venc;
ALTER TABLE contratos ADD CONSTRAINT ck_contrato_dia_venc CHECK (dia_vencimento IS NULL OR dia_vencimento BETWEEN 1 AND 31) NOT VALID;
ALTER TABLE contratos DROP CONSTRAINT IF EXISTS ck_contrato_mes_reaj;
ALTER TABLE contratos ADD CONSTRAINT ck_contrato_mes_reaj CHECK (mes_reajuste IS NULL OR mes_reajuste BETWEEN 1 AND 12) NOT VALID;

-- saldo: no mensal fixo as horas liberadas valem por mês (renovam todo mês); nos demais, pelo contrato todo
CREATE OR REPLACE VIEW vw_saldo_banco_horas AS
SELECT ct.id                                         AS contrato_id,
       ct.cliente_id,
       ct.descricao,
       ct.horas_pacote,
       ct.data_inicio,
       ct.data_fim                                   AS validade,
       round(coalesce(sum(a.minutos), 0) / 60.0, 2)  AS horas_consumidas,
       round(ct.horas_pacote - coalesce(sum(a.minutos), 0) / 60.0, 2) AS horas_saldo,
       round(100 * coalesce(sum(a.minutos), 0) / 60.0 / nullif(ct.horas_pacote, 0), 1) AS pct_consumido,
       CASE WHEN ct.tipo = 'mensal_fixo' THEN 'mensal' ELSE 'total' END AS periodo
  FROM contratos ct
  LEFT JOIN apontamentos a
         ON a.contrato_id = ct.id AND a.faturavel AND a.status <> 'rejeitado'
        AND (ct.tipo <> 'mensal_fixo' OR a.data >= date_trunc('month', current_date)::date)
 WHERE ct.horas_pacote IS NOT NULL
 GROUP BY ct.id;
COMMENT ON VIEW vw_saldo_banco_horas IS 'Saldo de horas dos contratos com horas liberadas (mensal fixo: saldo do mês corrente).';
