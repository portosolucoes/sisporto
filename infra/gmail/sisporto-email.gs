/**
 * SisPorto – ponte entre a caixa suporte@ e o sistema (Google Apps Script).
 *
 * Instalação (uma vez), logado na conta que RECEBE os e-mails do suporte@:
 *  1. script.google.com → Novo projeto → cole este arquivo → Salvar (nome: SisPorto E-mail)
 *  2. Configurações do projeto (engrenagem) → Propriedades do script → adicionar:
 *       SISPORTO_URL   = https://<endereço do sistema>   (sem barra no fim)
 *       SISPORTO_TOKEN = <valor do segredo sisporto-integracao-token>
 *  3. No editor, escolha a função "instalar" e clique em Executar → autorize o acesso.
 *     Pronto: a cada 5 minutos o script lê os e-mails novos e envia as notificações.
 */

const ENDERECO_SUPORTE = 'suporte@portosolucoes.com.br';
const NOME_REMETENTE = 'Suporte Porto Soluções';
const ROTULO_RECUSADO = 'SisPorto/Revisar';

function instalar() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('sincronizar').timeBased().everyMinutes(5).create();
  sincronizar();
}

function sincronizar() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    receber_();
    enviar_();
  } finally {
    lock.releaseLock();
  }
}

function config_() {
  var p = PropertiesService.getScriptProperties();
  var url = p.getProperty('SISPORTO_URL'), token = p.getProperty('SISPORTO_TOKEN');
  if (!url || !token) throw new Error('Configure SISPORTO_URL e SISPORTO_TOKEN nas propriedades do script.');
  return { url: url.replace(/\/$/, '') + '/integracao/email', token: token };
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

function receber_() {
  var busca = 'to:' + ENDERECO_SUPORTE + ' is:unread -from:me newer_than:7d';
  var threads = GmailApp.search(busca, 0, 25);
  var rotulo = GmailApp.getUserLabelByName(ROTULO_RECUSADO) || GmailApp.createLabel(ROTULO_RECUSADO);
  threads.forEach(function (t) {
    t.getMessages().forEach(function (m) {
      if (!m.isUnread()) return;
      var de = m.getFrom();
      var email = (de.match(/<([^>]+)>/) || [null, de])[1].trim();
      var res = chamar_('/entrada', {
        message_id: m.getId(), thread_id: t.getId(), de: email,
        assunto: m.getSubject(), texto: m.getPlainBody()
      });
      if (res.acao === 'recusado') t.addLabel(rotulo);
      m.markRead();
    });
  });
}

function enviar_() {
  var res = chamar_('/saida');
  var ok = [];
  (res.emails || []).forEach(function (e) {
    try {
      GmailApp.sendEmail(e.para, e.assunto, e.corpo, { name: NOME_REMETENTE, replyTo: ENDERECO_SUPORTE });
      ok.push(e.id);
    } catch (err) {
      console.error('Falha ao enviar ' + e.id + ': ' + err);
    }
  });
  if (ok.length) chamar_('/enviados', { ids: ok });
}
