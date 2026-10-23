-- =============================================================================
-- SisPorto – Dados iniciais (produção): expediente, feriados, SLA padrão
-- =============================================================================

-- Expediente: segunda a sexta, 8h–18h (ajustável)
INSERT INTO expediente (dia_semana, inicio, fim) VALUES
  (1, '08:00', '18:00'), (2, '08:00', '18:00'), (3, '08:00', '18:00'),
  (4, '08:00', '18:00'), (5, '08:00', '18:00');

-- Feriados nacionais 2026–2027 (+ Carnaval, Sexta-feira Santa e Corpus Christi)
INSERT INTO feriados (data, descricao) VALUES
  ('2026-01-01','Confraternização Universal'), ('2026-02-16','Carnaval'), ('2026-02-17','Carnaval'),
  ('2026-04-03','Sexta-feira Santa'), ('2026-04-21','Tiradentes'), ('2026-05-01','Dia do Trabalho'),
  ('2026-06-04','Corpus Christi'), ('2026-09-07','Independência'), ('2026-10-12','Nossa Senhora Aparecida'),
  ('2026-11-02','Finados'), ('2026-11-15','Proclamação da República'), ('2026-11-20','Consciência Negra'),
  ('2026-12-25','Natal'),
  ('2027-01-01','Confraternização Universal'), ('2027-02-08','Carnaval'), ('2027-02-09','Carnaval'),
  ('2027-03-26','Sexta-feira Santa'), ('2027-04-21','Tiradentes'), ('2027-05-01','Dia do Trabalho'),
  ('2027-05-27','Corpus Christi'), ('2027-09-07','Independência'), ('2027-10-12','Nossa Senhora Aparecida'),
  ('2027-11-02','Finados'), ('2027-11-15','Proclamação da República'), ('2027-11-20','Consciência Negra'),
  ('2027-12-25','Natal');

-- SLA padrão (documento de requisitos v1) – valores em minutos úteis
WITH p AS (INSERT INTO sla_politicas (nome, padrao) VALUES ('Padrão Porto', true) RETURNING id)
INSERT INTO sla_regras (politica_id, prioridade, minutos_1a_resposta, minutos_solucao)
SELECT p.id, x.prioridade::prioridade, x.r, x.s FROM p, (VALUES
  ('critica',  60,   240),   -- 1h  / 4h
  ('alta',    120,   480),   -- 2h  / 8h
  ('media',   240,  1200),   -- 4h  / 2 dias úteis
  ('baixa',   480,  3000)    -- 8h  / 5 dias úteis
) AS x(prioridade, r, s);

-- Administrador inicial
INSERT INTO usuarios (nome, email, perfil) VALUES ('Yuri', 'contato@portosolucoes.com.br', 'admin');
