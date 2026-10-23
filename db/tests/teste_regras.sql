-- Testes das regras do banco. Rodar em base descartável:
--   psql -v ON_ERROR_STOP=1 -f db/tests/teste_regras.sql
-- Cada bloco falha com EXCEPTION se a regra não se comportar como esperado.
BEGIN;

DO $$
DECLARE r timestamptz;
BEGIN
  -- sexta 23/10/2026 17:00 + 4h úteis = segunda 26/10 11:00
  r := fn_somar_minutos_uteis('2026-10-23 17:00-03', 240);
  ASSERT r = '2026-10-26 11:00-03', format('somar sexta: %s', r);
  -- antes do expediente: segunda 07:00 + 60 = 09:00
  r := fn_somar_minutos_uteis('2026-10-26 07:00-03', 60);
  ASSERT r = '2026-10-26 09:00-03', format('antes do expediente: %s', r);
  -- feriado: 30/10 sexta 17:00 + 2h → 02/11 é feriado (Finados) → terça 03/11 09:00
  r := fn_somar_minutos_uteis('2026-10-30 17:00-03', 120);
  ASSERT r = '2026-11-03 09:00-03', format('feriado: %s', r);
  -- 2 dias úteis = 1200 min a partir de seg 09:00 → qua 09:00
  r := fn_somar_minutos_uteis('2026-10-26 09:00-03', 1200);
  ASSERT r = '2026-10-28 09:00-03', format('2 dias: %s', r);
  -- entre: sexta 17:00 → segunda 10:00 = 60 + 120
  ASSERT fn_minutos_uteis_entre('2026-10-23 17:00-03', '2026-10-26 10:00-03') = 180, 'entre';
  RAISE NOTICE 'OK horário útil';
END $$;

-- Cenário completo
INSERT INTO clientes (razao_social, nome_fantasia, cnpj, dominio_email)
VALUES ('Cliente Teste Ltda', 'Cliente Teste', '11222333000181', 'clienteteste.com.br');
INSERT INTO usuarios (nome, email, perfil, cliente_id)
VALUES ('Maria Cliente', 'maria@clienteteste.com.br', 'cliente', (SELECT id FROM clientes WHERE cnpj='11222333000181'));
INSERT INTO usuarios (nome, email, perfil) VALUES ('Tecnico Um', 'tecnico@portosolucoes.com.br', 'tecnico');
INSERT INTO contratos (cliente_id, tipo, descricao, horas_pacote, data_inicio, data_fim)
VALUES ((SELECT id FROM clientes WHERE cnpj='11222333000181'), 'banco_horas', 'Pacote 20h', 20, '2026-10-01', '2027-03-31');

DO $$
DECLARE ch chamados%ROWTYPE; cli bigint; maria bigint; tec bigint; s text;
BEGIN
  SELECT id INTO cli FROM clientes WHERE cnpj='11222333000181';
  SELECT id INTO maria FROM usuarios WHERE email='maria@clienteteste.com.br';
  SELECT id INTO tec FROM usuarios WHERE email='tecnico@portosolucoes.com.br';

  -- perfil cliente sem empresa deve falhar
  BEGIN
    INSERT INTO usuarios (nome, email, perfil) VALUES ('X', 'x@x.com', 'cliente');
    RAISE EXCEPTION 'devia falhar';
  EXCEPTION WHEN check_violation THEN NULL; END;

  INSERT INTO chamados (cliente_id, solicitante_id, titulo, descricao, prioridade, criado_em)
  VALUES (cli, maria, 'Impressora parada', 'Não imprime', 'alta', '2026-10-23 17:00-03')
  RETURNING * INTO ch;
  ASSERT ch.numero = 1001, 'numero';
  ASSERT ch.contrato_id IS NOT NULL, 'contrato herdado';
  ASSERT ch.prazo_1a_resposta = '2026-10-26 09:00-03', format('prazo 1a: %s', ch.prazo_1a_resposta);
  ASSERT ch.prazo_solucao     = '2026-10-26 15:00-03', format('prazo sol: %s', ch.prazo_solucao);

  -- técnico responde → primeira resposta + em_atendimento
  INSERT INTO chamado_interacoes (chamado_id, autor_id, texto) VALUES (ch.id, tec, 'Verificando');
  SELECT * INTO ch FROM chamados WHERE id = ch.id;
  ASSERT ch.status = 'em_atendimento' AND ch.primeira_resposta_em IS NOT NULL, 'primeira resposta';

  -- pausa e cliente responde → volta
  UPDATE chamados SET status = 'aguardando_cliente' WHERE id = ch.id;
  INSERT INTO chamado_interacoes (chamado_id, autor_id, texto) VALUES (ch.id, maria, 'Feito');
  SELECT * INTO ch FROM chamados WHERE id = ch.id;
  ASSERT ch.status = 'em_atendimento', 'retomada pelo cliente';
  ASSERT (SELECT count(*) FROM chamado_status_historico WHERE chamado_id = ch.id) = 4, 'historico';

  -- prioridade crítica recalcula prazo (criado sex 17h + 4h úteis = seg 11h)
  UPDATE chamados SET prioridade = 'critica' WHERE id = ch.id;
  SELECT * INTO ch FROM chamados WHERE id = ch.id;
  ASSERT ch.prazo_solucao >= '2026-10-26 11:00-03', format('recalculo: %s', ch.prazo_solucao);

  -- horas: herda contrato, aprovação e saldo
  PERFORM set_config('sisporto.usuario_id', tec::text, true);
  INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, descricao)
  VALUES (tec, ch.id, '2026-10-26', 90, 'Troca de driver');
  INSERT INTO apontamentos (usuario_id, chamado_id, data, minutos, descricao, faturavel)
  VALUES (tec, ch.id, '2026-10-26', 30, 'Deslocamento', false);
  ASSERT (SELECT contrato_id FROM apontamentos LIMIT 1) = ch.contrato_id, 'apontamento herda contrato';
  ASSERT (SELECT horas_saldo FROM vw_saldo_banco_horas WHERE contrato_id = ch.contrato_id) = 18.5, 'saldo';
  ASSERT (SELECT count(*) FROM auditoria WHERE tabela = 'apontamentos' AND usuario_id = tec) = 2, 'auditoria';

  UPDATE chamados SET status = 'resolvido' WHERE id = ch.id;
  SELECT situacao_sla INTO s FROM vw_chamados_sla WHERE id = ch.id;
  RAISE NOTICE 'situação SLA ao resolver: %', s;
  RAISE NOTICE 'OK cenário completo';
END $$;

ROLLBACK;
