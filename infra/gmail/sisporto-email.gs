/**
 * SisPorto – ponte entre a conta suporte@ e o sistema (Google Apps Script).
 *
 * A cada 5 minutos:
 *   1. lê e-mails novos do suporte@ → chamados / respostas
 *   2. envia as notificações do sistema (chamados, senhas, NPS)
 *   3. cria/cancela eventos no Google Agenda (visitas e reuniões) com convite
 *   4. copia anexos dos chamados para o Google Drive (pasta SisPorto/<cliente>/<chamado>)
 *   5. dispara alertas de SLA no Google Chat e o NPS mensal
 *   6. 1x por dia: atualiza a planilha "SisPorto BI" (fonte do Looker Studio)
 *
 * Instalação/atualização: veja infra/gmail/LEIA-ME.md
 */

const ENDERECO_SUPORTE = 'suporte@portosolucoes.com.br';
const NOME_REMETENTE = 'Suporte Porto Soluções';
const ROTULO_RECUSADO = 'SisPorto/Revisar';
const PASTA_RAIZ = 'SisPorto – Chamados';
const PLANILHA_BI = 'SisPorto BI';
const DOMINIO = 'portosolucoes.com.br';

function instalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('sincronizar').timeBased().everyMinutes(5).create();
  sincronizar();
  atualizarBI();
}

function sincronizar() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    etapa_('e-mails recebidos', receber_);
    etapa_('tarefas', function () { chamar_('/integracao/tarefas'); });
    etapa_('e-mails a enviar', enviar_);
    etapa_('agenda', agenda_);
    etapa_('drive', drive_);
    var hoje = Utilities.formatDate(new Date(), 'America/Sao_Paulo', 'yyyy-MM-dd');
    var p = PropertiesService.getScriptProperties();
    if (p.getProperty('BI_ULTIMO') !== hoje) { etapa_('planilha BI', atualizarBI); p.setProperty('BI_ULTIMO', hoje); }
  } finally {
    lock.releaseLock();
  }
}

function etapa_(nome, fn) {
  try { fn(); } catch (e) { console.error('Falha em ' + nome + ': ' + e); }
}

function config_() {
  var p = PropertiesService.getScriptProperties();
  var url = p.getProperty('SISPORTO_URL'), token = p.getProperty('SISPORTO_TOKEN');
  if (!url || !token) throw new Error('Configure SISPORTO_URL e SISPORTO_TOKEN nas propriedades do script.');
  return { url: url.replace(/\/$/, ''), token: token };
}

function chamar_(rota, dados) {
  var c = config_();
  var r = UrlFetchApp.fetch(c.url + rota, {
    method: 'post', contentType: 'application/json', headers: { 'X-Token': c.token },
    payload: JSON.stringify(dados || {}), muteHttpExceptions: true
  });
  if (r.getResponseCode() !== 200) throw new Error(rota + ' respondeu ' + r.getResponseCode() + ': ' + r.getContentText().slice(0, 300));
  return JSON.parse(r.getContentText());
}

// 1. E-mails recebidos ---------------------------------------------------------------
function receber_() {
  var busca = 'to:' + ENDERECO_SUPORTE + ' is:unread -from:me newer_than:7d';
  var threads = GmailApp.search(busca, 0, 25);
  var rotulo = GmailApp.getUserLabelByName(ROTULO_RECUSADO) || GmailApp.createLabel(ROTULO_RECUSADO);
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      if (!m.isUnread()) return;
      var de = m.getFrom();
      var email = (de.match(/<([^>]+)>/) || [null, de])[1].trim();
      var res = chamar_('/integracao/email/entrada', {
        message_id: m.getId(), thread_id: t.getId(), de: email,
        assunto: m.getSubject(), texto: m.getPlainBody()
      });
      if (res.acao === 'recusado') t.addLabel(rotulo);
      m.markRead();
    });
  });
}

// 2. Notificações ---------------------------------------------------------------------
function enviar_() {
  var res = chamar_('/integracao/email/saida');
  var ok = [];
  (res.emails || []).forEach(function (e) {
    try {
      GmailApp.sendEmail(e.para, e.assunto, e.corpo, { name: NOME_REMETENTE, replyTo: ENDERECO_SUPORTE });
      ok.push(e.id);
    } catch (err) { console.error('Falha ao enviar ' + e.id + ': ' + err); }
  });
  if (ok.length) chamar_('/integracao/email/enviados', { ids: ok });
}

// 3. Google Agenda ----------------------------------------------------------------------
function agenda_() {
  var cal = CalendarApp.getDefaultCalendar();
  (chamar_('/integracao/agenda').itens || []).forEach(function (a) {
    if (a.status === 'pendente') {
      var ev = cal.createEvent(a.titulo_evento, new Date(a.inicio), new Date(a.fim), {
        description: (a.descricao || '') + '\n\nChamado #' + a.numero + ': ' + a.titulo + '\n' + (a.link || ''),
        location: a.local || '', guests: a.convidados, sendInvites: true
      });
      chamar_('/integracao/agenda/ok', { id: a.id, acao: 'criado', evento_id: ev.getId() });
    } else if (a.status === 'cancelar') {
      if (a.google_evento_id) {
        var ev2 = cal.getEventById(a.google_evento_id);
        if (ev2) ev2.deleteEvent();
      }
      chamar_('/integracao/agenda/ok', { id: a.id, acao: 'cancelado' });
    }
  });
}

// 4. Google Drive ------------------------------------------------------------------------
function pasta_(pai, nome) {
  var it = pai.getFoldersByName(nome);
  return it.hasNext() ? it.next() : pai.createFolder(nome);
}

function drive_() {
  var raizIt = DriveApp.getFoldersByName(PASTA_RAIZ);
  var raiz = raizIt.hasNext() ? raizIt.next() : DriveApp.createFolder(PASTA_RAIZ);
  try { raiz.setSharing(DriveApp.Access.DOMAIN, DriveApp.Permission.EDIT); } catch (e) { /* política do domínio */ }
  for (var i = 0; i < 3; i++) {  // até 3 anexos por rodada
    var a = chamar_('/integracao/drive').anexo;
    if (!a) return;
    var cliente = pasta_(raiz, a.cliente.replace(/[\/\\]/g, '-'));
    var chamado = pasta_(cliente, '#' + a.numero + ' – ' + a.titulo.replace(/[\/\\]/g, '-').slice(0, 80));
    var blob = Utilities.newBlob(Utilities.base64Decode(a.conteudo), a.tipo_mime, a.nome_arquivo);
    var arq = chamado.createFile(blob);
    chamar_('/integracao/drive/ok', { anexo_id: a.id, arquivo_id: arq.getId(), url: arq.getUrl(), pasta_url: chamado.getUrl() });
  }
}

// 6. Planilha de BI (Looker Studio) ---------------------------------------------------------
function atualizarBI() {
  var dados = chamar_('/integracao/bi').abas;
  var it = DriveApp.getFilesByName(PLANILHA_BI);
  var ss = it.hasNext() ? SpreadsheetApp.open(it.next()) : SpreadsheetApp.create(PLANILHA_BI);
  Object.keys(dados).forEach(function (nome) {
    var aba = ss.getSheetByName(nome) || ss.insertSheet(nome);
    aba.clearContents();
    var d = dados[nome];
    if (!d.colunas.length) return;
    aba.getRange(1, 1, 1, d.colunas.length).setValues([d.colunas]).setFontWeight('bold');
    if (d.linhas.length) aba.getRange(2, 1, d.linhas.length, d.colunas.length).setValues(d.linhas);
    aba.setFrozenRows(1);
  });
  var padrao = ss.getSheetByName('Página1') || ss.getSheetByName('Sheet1');
  if (padrao && ss.getSheets().length > 1) ss.deleteSheet(padrao);
}
