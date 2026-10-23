-- =============================================================================
-- SisPorto – Regras automáticas: horário útil, SLA, histórico e auditoria
-- Todo cálculo de horário útil usa o fuso America/Sao_Paulo.
-- =============================================================================

-- Soma N minutos ÚTEIS a um instante (respeita expediente e feriados)
CREATE OR REPLACE FUNCTION fn_somar_minutos_uteis(p_inicio timestamptz, p_minutos integer)
RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE
  t        timestamp := p_inicio AT TIME ZONE 'America/Sao_Paulo';
  restante integer   := greatest(p_minutos, 0);
  e        expediente%ROWTYPE;
  ini      timestamp;
  fim      timestamp;
  disp     integer;
  guarda   integer := 0;
BEGIN
  IF restante = 0 THEN RETURN p_inicio; END IF;
  LOOP
    guarda := guarda + 1;
    IF guarda > 3660 THEN RAISE EXCEPTION 'expediente não configurado'; END IF;
    SELECT * INTO e FROM expediente WHERE dia_semana = extract(dow FROM t)::int;
    IF NOT FOUND OR EXISTS (SELECT 1 FROM feriados WHERE data = t::date) THEN
      t := t::date + 1;                       -- próximo dia, 00:00
      CONTINUE;
    END IF;
    ini := t::date + e.inicio;
    fim := t::date + e.fim;
    IF t < ini THEN t := ini; END IF;
    IF t >= fim THEN t := t::date + 1; CONTINUE; END IF;
    disp := floor(extract(epoch FROM fim - t) / 60)::int;
    IF restante <= disp THEN
      RETURN (t + make_interval(mins => restante)) AT TIME ZONE 'America/Sao_Paulo';
    END IF;
    restante := restante - disp;
    t := t::date + 1;
  END LOOP;
END $$;

-- Minutos ÚTEIS entre dois instantes
CREATE OR REPLACE FUNCTION fn_minutos_uteis_entre(p_de timestamptz, p_ate timestamptz)
RETURNS integer LANGUAGE plpgsql STABLE AS $$
DECLARE
  a      timestamp := p_de  AT TIME ZONE 'America/Sao_Paulo';
  b      timestamp := p_ate AT TIME ZONE 'America/Sao_Paulo';
  d      date;
  e      expediente%ROWTYPE;
  ini    timestamp;
  fim    timestamp;
  total  numeric := 0;
BEGIN
  IF p_de IS NULL OR p_ate IS NULL OR b <= a THEN RETURN 0; END IF;
  d := a::date;
  WHILE d <= b::date LOOP
    SELECT * INTO e FROM expediente WHERE dia_semana = extract(dow FROM d)::int;
    IF FOUND AND NOT EXISTS (SELECT 1 FROM feriados WHERE data = d) THEN
      ini := greatest(d + e.inicio, a);
      fim := least(d + e.fim, b);
      IF fim > ini THEN total := total + extract(epoch FROM fim - ini) / 60; END IF;
    END IF;
    d := d + 1;
  END LOOP;
  RETURN floor(total)::int;
END $$;

-- Regra de SLA aplicável a um chamado (política do contrato ou padrão)
CREATE OR REPLACE FUNCTION fn_regra_sla(p_contrato_id bigint, p_prioridade prioridade)
RETURNS sla_regras LANGUAGE sql STABLE AS $$
  SELECT r.*
    FROM sla_regras r
   WHERE r.prioridade = p_prioridade
     AND r.politica_id = coalesce(
           (SELECT sla_politica_id FROM contratos WHERE id = p_contrato_id),
           (SELECT id FROM sla_politicas WHERE padrao))
$$;

-- Usuário da sessão (a aplicação faz: SET LOCAL sisporto.usuario_id = '12')
CREATE OR REPLACE FUNCTION fn_usuario_sessao() RETURNS bigint LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('sisporto.usuario_id', true), '')::bigint
$$;

-- ----------------------------------------------------------------------------
-- Gatilhos de chamados
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION tg_chamado_antes() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  r      sla_regras;
  pausa  integer;
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- contrato: se não informado e o cliente tem um único contrato ativo, usa ele
    IF NEW.contrato_id IS NULL THEN
      SELECT id INTO NEW.contrato_id FROM contratos
       WHERE cliente_id = NEW.cliente_id AND ativo
         AND (SELECT count(*) FROM contratos c2 WHERE c2.cliente_id = NEW.cliente_id AND c2.ativo) = 1;
    END IF;
    r := fn_regra_sla(NEW.contrato_id, NEW.prioridade);
    IF r.politica_id IS NOT NULL THEN
      NEW.prazo_1a_resposta := fn_somar_minutos_uteis(NEW.criado_em, r.minutos_1a_resposta);
      NEW.prazo_solucao     := fn_somar_minutos_uteis(NEW.criado_em, r.minutos_solucao);
    END IF;
    IF NEW.status = 'aguardando_cliente' THEN NEW.pausado_desde := NEW.criado_em; END IF;
    RETURN NEW;
  END IF;

  -- UPDATE ---------------------------------------------------------------
  NEW.atualizado_em := now();

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    -- entrou em pausa
    IF NEW.status = 'aguardando_cliente' THEN
      NEW.pausado_desde := now();
    END IF;
    -- saiu da pausa: estende o prazo de solução pelo tempo útil pausado
    IF OLD.status = 'aguardando_cliente' AND OLD.pausado_desde IS NOT NULL THEN
      pausa := fn_minutos_uteis_entre(OLD.pausado_desde, now());
      NEW.minutos_pausados := OLD.minutos_pausados + pausa;
      NEW.pausado_desde := NULL;
      IF NEW.prazo_solucao IS NOT NULL AND pausa > 0 THEN
        NEW.prazo_solucao := fn_somar_minutos_uteis(NEW.prazo_solucao, pausa);
      END IF;
    END IF;
    -- marcos
    IF NEW.status = 'resolvido' AND NEW.resolvido_em IS NULL THEN NEW.resolvido_em := now(); END IF;
    IF NEW.status = 'fechado'   AND NEW.fechado_em   IS NULL THEN
      NEW.fechado_em := now();
      NEW.resolvido_em := coalesce(NEW.resolvido_em, now());
    END IF;
    -- reabertura
    IF NEW.status IN ('novo','em_atendimento','aguardando_cliente') THEN
      NEW.resolvido_em := NULL; NEW.fechado_em := NULL;
    END IF;
  END IF;

  -- mudança de prioridade ou contrato: recalcula prazos a partir da abertura
  IF NEW.prioridade IS DISTINCT FROM OLD.prioridade
     OR NEW.contrato_id IS DISTINCT FROM OLD.contrato_id THEN
    r := fn_regra_sla(NEW.contrato_id, NEW.prioridade);
    IF r.politica_id IS NOT NULL THEN
      IF NEW.primeira_resposta_em IS NULL THEN
        NEW.prazo_1a_resposta := fn_somar_minutos_uteis(NEW.criado_em, r.minutos_1a_resposta);
      END IF;
      NEW.prazo_solucao := fn_somar_minutos_uteis(NEW.criado_em, r.minutos_solucao + NEW.minutos_pausados);
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER chamados_antes BEFORE INSERT OR UPDATE ON chamados
  FOR EACH ROW EXECUTE FUNCTION tg_chamado_antes();

CREATE OR REPLACE FUNCTION tg_chamado_historico() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO chamado_status_historico (chamado_id, de, para, usuario_id, em)
    VALUES (NEW.id, NULL, NEW.status, coalesce(fn_usuario_sessao(), NEW.solicitante_id), NEW.criado_em);
  ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO chamado_status_historico (chamado_id, de, para, usuario_id)
    VALUES (NEW.id, OLD.status, NEW.status, fn_usuario_sessao());
  END IF;
  RETURN NULL;
END $$;

CREATE TRIGGER chamados_historico AFTER INSERT OR UPDATE OF status ON chamados
  FOR EACH ROW EXECUTE FUNCTION tg_chamado_historico();

-- Interações: 1ª resposta da equipe e retomada quando o cliente responde
CREATE OR REPLACE FUNCTION tg_interacao_depois() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  perfil_autor perfil_usuario;
  ch           chamados%ROWTYPE;
BEGIN
  IF NEW.tipo <> 'publica' THEN RETURN NULL; END IF;
  SELECT perfil INTO perfil_autor FROM usuarios WHERE id = NEW.autor_id;
  SELECT * INTO ch FROM chamados WHERE id = NEW.chamado_id;

  IF perfil_autor IS NOT NULL AND perfil_autor <> 'cliente' THEN
    UPDATE chamados
       SET primeira_resposta_em = coalesce(primeira_resposta_em, NEW.criado_em),
           status = CASE WHEN status = 'novo' THEN 'em_atendimento'::status_chamado ELSE status END
     WHERE id = NEW.chamado_id;
  ELSIF ch.status = 'aguardando_cliente' THEN
    -- cliente (portal ou e-mail) respondeu: volta para atendimento
    UPDATE chamados SET status = 'em_atendimento' WHERE id = NEW.chamado_id;
  END IF;
  RETURN NULL;
END $$;

CREATE TRIGGER interacoes_depois AFTER INSERT ON chamado_interacoes
  FOR EACH ROW EXECUTE FUNCTION tg_interacao_depois();

-- ----------------------------------------------------------------------------
-- Apontamentos: herda o contrato do chamado
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION tg_apontamento_antes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.contrato_id IS NULL AND NEW.chamado_id IS NOT NULL THEN
    SELECT contrato_id INTO NEW.contrato_id FROM chamados WHERE id = NEW.chamado_id;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    NEW.atualizado_em := now();
    IF NEW.status <> 'pendente' AND OLD.status = 'pendente' THEN
      NEW.aprovado_em := coalesce(NEW.aprovado_em, now());
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER apontamentos_antes BEFORE INSERT OR UPDATE ON apontamentos
  FOR EACH ROW EXECUTE FUNCTION tg_apontamento_antes();

-- ----------------------------------------------------------------------------
-- atualizado_em genérico
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION tg_atualizado_em() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.atualizado_em := now(); RETURN NEW; END $$;

CREATE TRIGGER clientes_atualizado  BEFORE UPDATE ON clientes  FOR EACH ROW EXECUTE FUNCTION tg_atualizado_em();
CREATE TRIGGER usuarios_atualizado  BEFORE UPDATE ON usuarios  FOR EACH ROW EXECUTE FUNCTION tg_atualizado_em();
CREATE TRIGGER contratos_atualizado BEFORE UPDATE ON contratos FOR EACH ROW EXECUTE FUNCTION tg_atualizado_em();

-- ----------------------------------------------------------------------------
-- Auditoria (contratos, apontamentos e usuários)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION tg_auditoria() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO auditoria (tabela, registro_id, acao, usuario_id, dados_antes, dados_depois)
  VALUES (TG_TABLE_NAME,
          CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END,
          TG_OP, fn_usuario_sessao(),
          CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) - 'senha_hash' END,
          CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) - 'senha_hash' END);
  RETURN NULL;
END $$;

CREATE TRIGGER contratos_auditoria    AFTER INSERT OR UPDATE OR DELETE ON contratos    FOR EACH ROW EXECUTE FUNCTION tg_auditoria();
CREATE TRIGGER apontamentos_auditoria AFTER INSERT OR UPDATE OR DELETE ON apontamentos FOR EACH ROW EXECUTE FUNCTION tg_auditoria();
CREATE TRIGGER usuarios_auditoria     AFTER INSERT OR UPDATE OR DELETE ON usuarios     FOR EACH ROW EXECUTE FUNCTION tg_auditoria();
