-- 028: comentários das tabelas e colunas (base do dicionário de dados gerado pelo sistema)

COMMENT ON TABLE usuarios IS 'Pessoas que acessam o sistema: recursos da Porto (admin, técnico, financeiro, código R0001…) e usuários de clientes (perfil cliente, código C0001…).';
COMMENT ON COLUMN usuarios.codigo IS 'Chave de negócio do usuário (R0001 recurso / C0001 cliente). Gerado por trigger, serve para entrar no sistema.';
COMMENT ON COLUMN usuarios.perfil IS 'admin, tecnico, financeiro ou cliente. Somente admin promove a admin, o e-mail ADMIN_MASTER é sempre admin.';
COMMENT ON COLUMN usuarios.cliente_id IS 'Cliente ao qual o usuário pertence (somente perfil cliente).';
COMMENT ON COLUMN usuarios.senha_hash IS 'Hash da senha (werkzeug/scrypt). Nunca a senha em texto.';
COMMENT ON COLUMN usuarios.senha_provisoria IS 'Senha temporária gerada pelo sistema: troca obrigatória no próximo acesso.';
COMMENT ON COLUMN usuarios.custo_hora IS 'Custo médio da hora do recurso (R$). Usado na rentabilidade.';
COMMENT ON COLUMN usuarios.recebe_nps IS 'Usuário de cliente que recebe a pesquisa NPS mensal.';
COMMENT ON COLUMN usuarios.totp_segredo IS 'Segredo da verificação em duas etapas (TOTP) dos recursos.';
COMMENT ON COLUMN usuarios.totp_ativo IS 'Verificação em duas etapas ligada.';
COMMENT ON COLUMN usuarios.ultimo_acesso IS 'Data/hora do último login.';

COMMENT ON TABLE clientes IS 'Empresas atendidas pela Porto Soluções.';
COMMENT ON COLUMN clientes.cnpj IS 'CNPJ só com dígitos (validado).';
COMMENT ON COLUMN clientes.dominio_email IS 'Domínio de e-mail do cliente: qualquer pessoa desse domínio pode abrir chamado por e-mail.';
COMMENT ON COLUMN clientes.responsavel_id IS 'Recurso responsável pela conta (gerente de conta).';
COMMENT ON COLUMN clientes.ativo IS 'Cliente inativo não abre chamados nem recebe novos usuários.';

COMMENT ON TABLE contratos IS 'Contratos do cliente: mensal fixo, banco de horas, hora avulsa, projeto ou proposta avulsa.';
COMMENT ON COLUMN contratos.tipo IS 'mensal_fixo, banco_horas, hora_avulsa, projeto, proposta_avulsa.';
COMMENT ON COLUMN contratos.valor_mensal IS 'Mensalidade (mensal fixo).';
COMMENT ON COLUMN contratos.franquia_horas IS 'Horas incluídas por mês no mensal fixo, acima disso é excedente.';
COMMENT ON COLUMN contratos.valor_hora IS 'Valor da hora de venda para este cliente (avulsa e excedentes).';
COMMENT ON COLUMN contratos.horas_pacote IS 'Horas liberadas do contrato/projeto (controle de saldo e rateio entre chamados).';
COMMENT ON COLUMN contratos.valor_total IS 'Valor total do projeto ou proposta.';
COMMENT ON COLUMN contratos.sla_politica_id IS 'Política de SLA (prazos por prioridade), vazio = política padrão.';
COMMENT ON COLUMN contratos.tipo_servico_id IS 'Tipo do projeto (suporte, infra, desenvolvimento…), tipo padrão dos apontamentos.';

COMMENT ON TABLE chamados IS 'Chamados (tickets) de suporte abertos pelo portal, por e-mail ou pela equipe.';
COMMENT ON COLUMN chamados.numero IS 'Número exibido ao cliente (#1001…).';
COMMENT ON COLUMN chamados.prioridade IS 'critica, alta, media, baixa.';
COMMENT ON COLUMN chamados.status IS 'novo, em_atendimento, aguardando_cliente, resolvido, fechado.';
COMMENT ON COLUMN chamados.origem IS 'portal, email ou interno (aberto pela equipe).';
COMMENT ON COLUMN chamados.prazo_1a_resposta IS 'Prazo de primeira resposta (SLA em horário comercial).';
COMMENT ON COLUMN chamados.prazo_solucao IS 'Prazo de solução (SLA em horário comercial, pausado aguardando cliente).';
COMMENT ON COLUMN chamados.minutos_pausados IS 'Minutos úteis em que o SLA ficou pausado (aguardando cliente).';
COMMENT ON COLUMN chamados.horas_alocadas IS 'Horas do contrato reservadas para este chamado (rateio). Vazio = usa o saldo livre do contrato.';
COMMENT ON COLUMN chamados.avaliacao IS 'Nota do cliente (1 a 5) ao encerrar.';
COMMENT ON COLUMN chamados.alerta_risco_em IS 'Quando foi enviado o alerta de SLA em risco ao Google Chat.';
COMMENT ON COLUMN chamados.alerta_estouro_em IS 'Quando foi enviado o alerta de SLA estourado ao Google Chat.';
COMMENT ON COLUMN chamados.drive_pasta_url IS 'Pasta do chamado no Google Drive.';

COMMENT ON TABLE chamado_interacoes IS 'Mensagens do chamado (públicas e notas internas), inclusive as recebidas por e-mail.';
COMMENT ON TABLE chamado_status_historico IS 'Histórico de mudanças de status dos chamados.';
COMMENT ON TABLE chamado_anexos IS 'Anexos compactados (até 20 MB) dos chamados, cópia no Google Drive.';
COMMENT ON TABLE apontamentos IS 'Horas trabalhadas pelos recursos, em chamados ou diretamente em contratos.';
COMMENT ON COLUMN apontamentos.minutos IS 'Duração em minutos.';
COMMENT ON COLUMN apontamentos.faturavel IS 'Entra na cobrança/consumo do contrato (não faturável = cortesia).';
COMMENT ON COLUMN apontamentos.status IS 'pendente, aprovado ou rejeitado (aprovação pelo administrador).';
COMMENT ON COLUMN apontamentos.motivo_rejeicao IS 'Motivo informado pelo administrador ao rejeitar.';
COMMENT ON COLUMN apontamentos.contrato_id IS 'Contrato consumido (preenchido pelo chamado quando vazio).';
COMMENT ON TABLE cronometros IS 'Cronômetro de atendimento por chamado e recurso (rodando ou pausado).';
COMMENT ON TABLE tipos_servico IS 'Tipos de serviço editáveis (help desk, infraestrutura, desenvolvimento…).';
COMMENT ON TABLE sla_politicas IS 'Políticas de SLA, cada contrato aponta para uma (ou usa a padrão).';
COMMENT ON TABLE sla_regras IS 'Prazos em minutos úteis por prioridade de cada política de SLA.';
COMMENT ON TABLE expediente IS 'Horário comercial por dia da semana (base do cálculo de SLA).';
COMMENT ON TABLE feriados IS 'Feriados excluídos do cálculo de SLA.';
COMMENT ON TABLE permissoes IS 'Exceções de acesso por rotina para cada recurso (o padrão vem do perfil).';
COMMENT ON TABLE auditoria IS 'Registro de alterações (antes/depois) nas tabelas principais, com o usuário.';
COMMENT ON TABLE configuracoes IS 'Configurações do sistema (logo, webhook do Chat, NPS etc.).';
COMMENT ON TABLE emails_saida IS 'Fila de e-mails a enviar (o Apps Script do suporte@ envia e marca enviado_em).';
COMMENT ON TABLE emails_recusados IS 'E-mails recebidos que não viraram chamado (remetente desconhecido).';
COMMENT ON TABLE senha_tokens IS 'Links de recuperação de senha (só o hash do token, validade de 60 min).';
COMMENT ON TABLE agendamentos IS 'Visitas e reuniões ligadas a chamados (viram eventos no Google Agenda).';
COMMENT ON TABLE nps_respostas IS 'Pesquisa NPS mensal enviada e respondida pelos usuários de clientes.';
COMMENT ON TABLE consultas_salvas IS 'Consultas SQL salvas pelo administrador.';
COMMENT ON TABLE consultas_log IS 'Registro das consultas SQL executadas pelo administrador.';
COMMENT ON TABLE schema_migracoes IS 'Controle das migrações de banco já aplicadas.';

COMMENT ON VIEW vw_chamados_sla IS 'Chamados com situação do SLA (no prazo, em risco, estourado) e nomes de cliente/responsável.';
COMMENT ON VIEW vw_saldo_banco_horas IS 'Saldo de horas dos contratos com horas liberadas.';
COMMENT ON VIEW vw_horas_mes IS 'Horas por contrato e mês (total, faturáveis aprovadas, pendentes).';
COMMENT ON VIEW vw_franquia_mes IS 'Consumo da franquia mensal e excedentes dos contratos mensais.';
COMMENT ON VIEW vw_bi_apontamentos IS 'Apontamentos com custo e receita estimada (fonte do Looker Studio).';
