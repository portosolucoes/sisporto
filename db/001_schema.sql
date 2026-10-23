-- =============================================================================
-- SisPorto – Banco de dados da Fase 1 (Help Desk, contratos, horas)
-- PostgreSQL 16 · Cloud SQL
-- Convenções: nomes em português, snake_case, datas em timestamptz (UTC no banco,
-- exibidas em America/Sao_Paulo), valores monetários numeric(12,2).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS citext;

-- ----------------------------------------------------------------------------
-- Tipos
-- ----------------------------------------------------------------------------
CREATE TYPE perfil_usuario   AS ENUM ('admin', 'tecnico', 'financeiro', 'cliente');
CREATE TYPE tipo_contrato    AS ENUM ('mensal_fixo', 'banco_horas', 'hora_avulsa', 'projeto', 'proposta_avulsa');
CREATE TYPE prioridade       AS ENUM ('critica', 'alta', 'media', 'baixa');
CREATE TYPE status_chamado   AS ENUM ('novo', 'em_atendimento', 'aguardando_cliente', 'resolvido', 'fechado');
CREATE TYPE origem_chamado   AS ENUM ('portal', 'email', 'interno');
CREATE TYPE tipo_interacao   AS ENUM ('publica', 'interna', 'sistema');
CREATE TYPE status_apontamento AS ENUM ('pendente', 'aprovado', 'rejeitado');

-- ----------------------------------------------------------------------------
-- Clientes e usuários
-- ----------------------------------------------------------------------------
CREATE TABLE clientes (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  razao_social     text        NOT NULL,
  nome_fantasia    text,
  cnpj             varchar(14) UNIQUE CHECK (cnpj ~ '^[0-9]{14}$'),
  email_financeiro citext,
  telefone         text,
  endereco         text,
  dominio_email    citext,      -- ex.: empresa.com.br → e-mails desse domínio viram chamados do cliente
  observacoes      text,
  ativo            boolean     NOT NULL DEFAULT true,
  criado_em        timestamptz NOT NULL DEFAULT now(),
  atualizado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_clientes_dominio ON clientes (dominio_email) WHERE dominio_email IS NOT NULL;

CREATE TABLE usuarios (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome           text           NOT NULL,
  email          citext         NOT NULL UNIQUE,
  perfil         perfil_usuario NOT NULL,
  cliente_id     bigint         REFERENCES clientes(id),
  senha_hash     text,          -- só clientes; equipe interna entra com Google
  telefone       text,
  ativo          boolean        NOT NULL DEFAULT true,
  ultimo_acesso  timestamptz,
  criado_em      timestamptz    NOT NULL DEFAULT now(),
  atualizado_em  timestamptz    NOT NULL DEFAULT now(),
  -- cliente pertence a uma empresa; equipe interna não
  CONSTRAINT ck_usuario_cliente CHECK ((perfil = 'cliente') = (cliente_id IS NOT NULL))
);
CREATE INDEX ix_usuarios_cliente ON usuarios (cliente_id);

-- ----------------------------------------------------------------------------
-- Calendário útil e SLA
-- ----------------------------------------------------------------------------
CREATE TABLE expediente (
  dia_semana  smallint PRIMARY KEY CHECK (dia_semana BETWEEN 0 AND 6),  -- 0 = domingo
  inicio      time NOT NULL,
  fim         time NOT NULL CHECK (fim > inicio)
);

CREATE TABLE feriados (
  data       date PRIMARY KEY,
  descricao  text NOT NULL
);

CREATE TABLE sla_politicas (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nome       text    NOT NULL UNIQUE,
  padrao     boolean NOT NULL DEFAULT false,
  criado_em  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_sla_uma_padrao ON sla_politicas (padrao) WHERE padrao;

CREATE TABLE sla_regras (
  politica_id            bigint     NOT NULL REFERENCES sla_politicas(id) ON DELETE CASCADE,
  prioridade             prioridade NOT NULL,
  minutos_1a_resposta    integer    NOT NULL CHECK (minutos_1a_resposta > 0),
  minutos_solucao        integer    NOT NULL CHECK (minutos_solucao > 0),
  PRIMARY KEY (politica_id, prioridade)
);

-- ----------------------------------------------------------------------------
-- Contratos
-- ----------------------------------------------------------------------------
CREATE TABLE contratos (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cliente_id        bigint        NOT NULL REFERENCES clientes(id),
  tipo              tipo_contrato NOT NULL,
  descricao         text          NOT NULL,
  sla_politica_id   bigint        REFERENCES sla_politicas(id),   -- nulo = política padrão
  valor_mensal      numeric(12,2) CHECK (valor_mensal >= 0),      -- mensal_fixo
  franquia_horas    numeric(7,2)  CHECK (franquia_horas >= 0),    -- mensal_fixo (opcional)
  valor_hora        numeric(12,2) CHECK (valor_hora >= 0),        -- excedente / avulsa
  horas_pacote      numeric(7,2)  CHECK (horas_pacote > 0),       -- banco_horas
  valor_total       numeric(12,2) CHECK (valor_total >= 0),       -- projeto / proposta
  data_inicio       date          NOT NULL,
  data_fim          date          CHECK (data_fim IS NULL OR data_fim >= data_inicio),
  dia_vencimento    smallint      CHECK (dia_vencimento BETWEEN 1 AND 28),
  indice_reajuste   text,         -- IPCA, IGP-M...
  mes_reajuste      smallint      CHECK (mes_reajuste BETWEEN 1 AND 12),
  ativo             boolean       NOT NULL DEFAULT true,
  criado_em         timestamptz   NOT NULL DEFAULT now(),
  atualizado_em     timestamptz   NOT NULL DEFAULT now(),
  CONSTRAINT ck_contrato_campos CHECK (
       (tipo <> 'mensal_fixo'     OR valor_mensal IS NOT NULL)
   AND (tipo <> 'banco_horas'     OR horas_pacote IS NOT NULL)
   AND (tipo <> 'hora_avulsa'     OR valor_hora   IS NOT NULL)
   AND (tipo NOT IN ('projeto','proposta_avulsa') OR valor_total IS NOT NULL)
  )
);
CREATE INDEX ix_contratos_cliente ON contratos (cliente_id) WHERE ativo;

-- ----------------------------------------------------------------------------
-- Chamados
-- ----------------------------------------------------------------------------
CREATE SEQUENCE chamado_numero_seq START WITH 1001;

CREATE TABLE chamados (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  numero                 integer        NOT NULL UNIQUE DEFAULT nextval('chamado_numero_seq'),
  cliente_id             bigint         NOT NULL REFERENCES clientes(id),
  contrato_id            bigint         REFERENCES contratos(id),
  solicitante_id         bigint         REFERENCES usuarios(id),
  solicitante_email      citext,        -- quem abriu por e-mail sem ter usuário
  responsavel_id         bigint         REFERENCES usuarios(id),
  titulo                 text           NOT NULL,
  descricao              text           NOT NULL,
  categoria              text,
  prioridade             prioridade     NOT NULL DEFAULT 'media',
  status                 status_chamado NOT NULL DEFAULT 'novo',
  origem                 origem_chamado NOT NULL DEFAULT 'portal',
  prazo_1a_resposta      timestamptz,
  prazo_solucao          timestamptz,
  primeira_resposta_em   timestamptz,
  resolvido_em           timestamptz,
  fechado_em             timestamptz,
  minutos_pausados       integer        NOT NULL DEFAULT 0,   -- tempo útil em "aguardando_cliente"
  pausado_desde          timestamptz,
  avaliacao              smallint       CHECK (avaliacao BETWEEN 1 AND 5),
  avaliacao_comentario   text,
  email_thread_id        text           UNIQUE,              -- Gmail threadId (chamado por e-mail)
  criado_em              timestamptz    NOT NULL DEFAULT now(),
  atualizado_em          timestamptz    NOT NULL DEFAULT now(),
  CONSTRAINT ck_chamado_solicitante CHECK (solicitante_id IS NOT NULL OR solicitante_email IS NOT NULL)
);
CREATE INDEX ix_chamados_cliente_status ON chamados (cliente_id, status);
CREATE INDEX ix_chamados_responsavel    ON chamados (responsavel_id) WHERE status NOT IN ('resolvido','fechado');
CREATE INDEX ix_chamados_prazo          ON chamados (prazo_solucao)  WHERE status NOT IN ('resolvido','fechado');

CREATE TABLE chamado_interacoes (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id        bigint         NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  autor_id          bigint         REFERENCES usuarios(id),     -- nulo = sistema ou e-mail externo
  autor_email       citext,
  tipo              tipo_interacao NOT NULL DEFAULT 'publica',
  texto             text           NOT NULL,
  email_message_id  text           UNIQUE,                      -- evita importar o mesmo e-mail 2x
  criado_em         timestamptz    NOT NULL DEFAULT now()
);
CREATE INDEX ix_interacoes_chamado ON chamado_interacoes (chamado_id, criado_em);

CREATE TABLE chamado_anexos (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id       bigint NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  interacao_id     bigint REFERENCES chamado_interacoes(id) ON DELETE CASCADE,
  nome_arquivo     text   NOT NULL,
  caminho_storage  text   NOT NULL,      -- gs://bucket/...
  tamanho_bytes    bigint NOT NULL CHECK (tamanho_bytes >= 0),
  tipo_mime        text,
  enviado_por      bigint REFERENCES usuarios(id),
  criado_em        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chamado_status_historico (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id  bigint         NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  de          status_chamado,
  para        status_chamado NOT NULL,
  usuario_id  bigint         REFERENCES usuarios(id),
  em          timestamptz    NOT NULL DEFAULT now()
);
CREATE INDEX ix_hist_chamado ON chamado_status_historico (chamado_id, em);

-- ----------------------------------------------------------------------------
-- Apontamento de horas
-- ----------------------------------------------------------------------------
CREATE TABLE apontamentos (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id     bigint             NOT NULL REFERENCES usuarios(id),
  chamado_id     bigint             REFERENCES chamados(id),
  tarefa_id      bigint,            -- FK para tarefas na Fase 2
  contrato_id    bigint             REFERENCES contratos(id),
  data           date               NOT NULL,
  minutos        integer            NOT NULL CHECK (minutos > 0 AND minutos <= 24*60),
  faturavel      boolean            NOT NULL DEFAULT true,
  descricao      text               NOT NULL,
  status         status_apontamento NOT NULL DEFAULT 'pendente',
  aprovado_por   bigint             REFERENCES usuarios(id),
  aprovado_em    timestamptz,
  criado_em      timestamptz        NOT NULL DEFAULT now(),
  atualizado_em  timestamptz        NOT NULL DEFAULT now(),
  CONSTRAINT ck_apontamento_origem CHECK (chamado_id IS NOT NULL OR tarefa_id IS NOT NULL),
  CONSTRAINT ck_apontamento_aprov  CHECK ((status = 'pendente') = (aprovado_por IS NULL))
);
CREATE INDEX ix_apont_contrato_data ON apontamentos (contrato_id, data);
CREATE INDEX ix_apont_usuario_data  ON apontamentos (usuario_id, data);
CREATE INDEX ix_apont_chamado       ON apontamentos (chamado_id);

-- ----------------------------------------------------------------------------
-- Auditoria
-- ----------------------------------------------------------------------------
CREATE TABLE auditoria (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tabela       text        NOT NULL,
  registro_id  bigint      NOT NULL,
  acao         text        NOT NULL CHECK (acao IN ('INSERT','UPDATE','DELETE')),
  usuario_id   bigint,     -- definido pela aplicação: SET LOCAL sisporto.usuario_id = '...'
  dados_antes  jsonb,
  dados_depois jsonb,
  em           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_auditoria_registro ON auditoria (tabela, registro_id);
