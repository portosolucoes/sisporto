// Filtra contratos e contatos pelo cliente escolhido (tela "Novo chamado")
(function () {
  var cli = document.getElementById('cliente_id');
  if (!cli) return;
  function filtrar() {
    ['contrato_id', 'solicitante_id'].forEach(function (id) {
      var sel = document.getElementById(id);
      if (!sel) return;
      Array.prototype.forEach.call(sel.options, function (o) {
        if (!o.dataset.cliente) return;
        var mostra = o.dataset.cliente === cli.value;
        o.hidden = !mostra; o.disabled = !mostra;
        if (!mostra && o.selected) sel.value = '';
      });
    });
  }
  cli.addEventListener('change', filtrar); filtrar();
})();
// Marcar/desmarcar todos (aprovação de horas)
(function () {
  var t = document.getElementById('marcar-todos');
  if (!t) return;
  t.addEventListener('change', function () {
    document.querySelectorAll('input[name=ids]').forEach(function (c) { c.checked = t.checked; });
  });
})();

// Menu recolhível (celular)
(function () {
  var btn = document.querySelector('.menu-btn'), sombra = document.querySelector('.sombra');
  if (!btn) return;
  function abrir(sim) {
    document.body.classList.toggle('menu-aberto', sim);
    btn.setAttribute('aria-expanded', sim ? 'true' : 'false');
    if (sombra) sombra.hidden = !sim;
  }
  btn.addEventListener('click', function () { abrir(!document.body.classList.contains('menu-aberto')); });
  if (sombra) sombra.addEventListener('click', function () { abrir(false); });
  document.querySelectorAll('.side nav a').forEach(function (a) { a.addEventListener('click', function () { abrir(false); }); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') abrir(false); });
})();

// Anexos: só compactados até 20 MB – valida antes de enviar
(function () {
  var exts = ['.zip', '.rar', '.7z', '.gz', '.tgz', '.bz2', '.xz'];
  document.querySelectorAll('input[type=file][data-limite]').forEach(function (inp) {
    var erro = inp.parentNode.querySelector('.anexo-erro');
    inp.addEventListener('change', function () {
      var f = inp.files[0], msg = '';
      if (f) {
        var nome = f.name.toLowerCase();
        if (f.size > Number(inp.dataset.limite)) {
          msg = 'O arquivo tem ' + (f.size / 1048576).toFixed(1) + ' MB e o limite é 20 MB. Ele não será enviado: ' +
                'coloque-o no Google Drive (ou similar) e cole o link na mensagem.';
        } else if (!exts.some(function (e) { return nome.endsWith(e); })) {
          msg = 'Só são aceitos arquivos compactados (.zip, .rar, .7z). Compacte o arquivo e escolha de novo.';
        }
      }
      if (erro) { erro.textContent = msg; erro.hidden = !msg; }
      if (msg) inp.value = '';
    });
  });
})();

// Domínio de e-mail do cliente: preenche a partir do e-mail e limpa o que for digitado
(function () {
  var email = document.getElementById('email_financeiro'), dom = document.getElementById('dominio_email');
  if (!email || !dom) return;
  var publicos = ['gmail.com','googlemail.com','hotmail.com','hotmail.com.br','outlook.com','outlook.com.br','live.com',
    'msn.com','yahoo.com','yahoo.com.br','icloud.com','me.com','uol.com.br','bol.com.br','terra.com.br','ig.com.br',
    'globo.com','globomail.com','zipmail.com.br','proton.me','protonmail.com','aol.com'];
  var automatico = !dom.value;
  function limpar(v) {
    v = (v || '').trim().toLowerCase();
    if (v.indexOf('@') >= 0) v = v.split('@').pop();
    v = v.replace(/^[a-z]+:\/\//, '').split('/')[0].replace(/^www\./, '').replace(/[^a-z0-9.-]/g, '');
    return v;
  }
  email.addEventListener('input', function () {
    if (!automatico) return;
    var d = limpar(email.value.indexOf('@') >= 0 ? email.value : '');
    dom.value = publicos.indexOf(d) >= 0 ? '' : d;
  });
  dom.addEventListener('input', function () { automatico = !dom.value; });
  dom.addEventListener('blur', function () { dom.value = limpar(dom.value); });
})();

// Cronômetro do chamado: conta na tela, desconta em tempo real os outros cronômetros do mesmo saldo,
// avisa quando falta pouco e, no fim do saldo, pausa e pede para encerrar o apontamento
(function () {
  var el = document.querySelector('.cronometro');
  if (!el) return;
  var seg, rodando, limite, outros, inicio, enviado = false, avisado10 = false;
  var alerta = document.querySelector('.cron-alerta'), disp = document.querySelector('.cron-disp');
  function carregar(d) {
    seg = Number(d.segundos || 0); rodando = d.rodando === true || d.rodando === '1';
    limite = d.limite === '' || d.limite === null || d.limite === undefined ? null : Number(d.limite);
    outros = Number(d.outros_rodando !== undefined ? d.outros_rodando : (d.outros || 0)); inicio = Date.now();
  }
  carregar(el.dataset);
  function fmt(s) { s = Math.max(0, s); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return [h, m, x].map(function (n) { return String(n).padStart(2, '0'); }).join(':'); }
  function avisar(txt) { if (!alerta) return; alerta.hidden = false; alerta.textContent = txt; }
  function tick() {
    var dec = Math.floor((Date.now() - inicio) / 1000);
    var atual = rodando ? seg + dec : seg;
    var lim = limite === null ? null : limite - dec * outros;   // os outros também estão consumindo
    if (lim !== null) {
      var falta = lim - atual;
      if (disp) disp.textContent = fmt(Math.max(falta, 0)).slice(0, 5).replace(':', 'h');
      if (rodando && falta <= 600 && falta > 0 && !avisado10) {
        avisado10 = true;
        avisar('Faltam menos de 10 minutos de saldo de horas. Prepare-se para encerrar a atuação.');
      }
      if (falta <= 0) {
        atual = Math.max(lim, 0); el.classList.add('esgotado');
        avisar('O saldo de horas acabou: pare a atuação e finalize o apontamento.');
        var f = document.getElementById('form-pausar');
        if (rodando && f && !enviado) { enviado = true; f.submit(); }
      }
    }
    el.textContent = fmt(atual);
  }
  tick();
  setInterval(tick, 1000);
  // sincroniza com o servidor (outros recursos/chamados podem ter iniciado ou parado)
  if (el.dataset.estado && window.fetch) setInterval(function () {
    fetch(el.dataset.estado, {credentials: 'same-origin'}).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && !enviado) carregar(d); }).catch(function () {});
  }, 30000);
  if (location.hash === '#cronometro' && document.getElementById('form-finalizar') && el.classList.contains('esgotado')) {
    document.querySelector('#form-finalizar textarea').focus();
  }
})();

// cadastro do recurso: perfil admin marca (e trava) todas as rotinas; trocar de perfil aplica o padrão do novo perfil
(function () {
  var sel = document.getElementById('perfil-recurso');
  if (!sel) return;
  var original = sel.value;
  var caixas = document.querySelectorAll('.rotinas input[type=checkbox]');
  var guardado = {};
  caixas.forEach(function (c) { guardado[c.value] = c.checked; });
  sel.addEventListener('change', function () {
    var p = sel.value;
    document.getElementById('nota-admin').hidden = p !== 'admin';
    caixas.forEach(function (c) {
      if (p === 'admin') { c.checked = true; c.disabled = true; return; }
      if (c.dataset.soAdmin === '1') { c.checked = false; c.disabled = true; return; }
      c.disabled = false;
      c.checked = p === original ? guardado[c.value] : (c.dataset.perfis || '').split(' ').indexOf(p) >= 0;
    });
  });
})();

// consultas SQL: Ctrl+Enter executa; filtro do dicionário de dados
(function () {
  var sql = document.getElementById('sql');
  if (sql) sql.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); sql.form.querySelector('[value=executar]').click(); }
  });
  var f = document.getElementById('filtro-tabelas');
  if (f) f.addEventListener('input', function () {
    var q = f.value.toLowerCase();
    document.querySelectorAll('.tabela-dic').forEach(function (d) {
      d.hidden = q && d.dataset.busca.toLowerCase().indexOf(q) < 0;
    });
  });
})();

// menu: lembra quais pastas ficaram abertas
(function () {
  var chave = 'sisporto.pastas';
  var abertas = {};
  try { abertas = JSON.parse(localStorage.getItem(chave) || '{}'); } catch (e) { abertas = {}; }
  document.querySelectorAll('details.pasta').forEach(function (d) {
    var n = d.dataset.pasta;
    if (abertas[n] && !d.open) d.open = true;
    d.addEventListener('toggle', function () {
      abertas[n] = d.open;
      try { localStorage.setItem(chave, JSON.stringify(abertas)); } catch (e) { /* sem armazenamento */ }
    });
  });
})();

// contrato: sugestões (valor mensal = valor hora × franquia; horas liberadas = franquia), itens e vigência
(function () {
  var f = document.getElementById('form-contrato');
  if (!f) return;
  function num(v) { v = (v || '').replace(/R\$|\s/g, ''); if (v.indexOf(',') >= 0) v = v.replace(/\./g, '').replace(',', '.'); var n = parseFloat(v); return isNaN(n) ? null : n; }
  function br(n) { return n.toFixed(2).replace('.', ','); }
  var tipo = f.elements.tipo, franq = f.elements.franquia_horas, vh = f.elements.valor_hora,
      vm = f.elements.valor_mensal, hp = f.elements.horas_pacote;
  // só sobrescreve o que o sistema sugeriu (ou campo vazio)
  [vm, hp].forEach(function (el) { el.dataset.auto = el.value ? '0' : '1'; el.addEventListener('input', function () { el.dataset.auto = '0'; }); });
  function sugerir() {
    var fr = num(franq.value), h = num(vh.value), t = tipo.value;
    if (t === 'mensal_fixo' && fr !== null && h !== null && (vm.dataset.auto === '1' || !vm.value)) { vm.value = br(fr * h); vm.dataset.auto = '1'; }
    if (['mensal_fixo', 'projeto', 'proposta_avulsa'].indexOf(t) >= 0 && fr !== null && (hp.dataset.auto === '1' || !hp.value)) { hp.value = String(fr).replace('.', ','); hp.dataset.auto = '1'; }
  }
  [franq, vh, tipo].forEach(function (el) { el.addEventListener('input', sugerir); el.addEventListener('change', sugerir); });
  // vigência: fim não pode ser antes do início
  var di = document.getElementById('data_inicio'), df = document.getElementById('data_fim');
  function vig() { df.min = di.value || ''; df.setCustomValidity(df.value && di.value && df.value < di.value ? 'A data de fim não pode ser anterior ao início.' : ''); }
  di.addEventListener('change', vig); df.addEventListener('change', vig); vig();
  // dia de vencimento / mês do reajuste
  [['dia_vencimento', 31, 'Dia de vencimento: de 1 a 31.'], ['mes_reajuste', 12, 'Mês do reajuste: de 1 a 12.']].forEach(function (x) {
    var el = f.elements[x[0]]; if (!el) return;
    el.addEventListener('input', function () { var v = parseInt(el.value, 10); el.setCustomValidity(el.value && (isNaN(v) || v < 1 || v > x[1]) ? x[2] : ''); });
  });
  // itens
  var corpo = document.getElementById('itens'), soma = document.getElementById('soma-itens');
  function somar() {
    var t = 0, todos = true, algum = false;
    corpo.querySelectorAll('tr.item').forEach(function (tr) {
      if (!tr.querySelector('select').value) return;
      algum = true; var h = num(tr.querySelector('[name=item_horas]').value);
      if (h === null) todos = false; else t += h;
    });
    soma.textContent = algum && t ? 'Soma das horas dos itens: ' + String(t).replace('.', ',') + 'h' : '';
    if (algum && todos && t && (hp.dataset.auto === '1' || !hp.value)) { hp.value = String(t).replace('.', ','); hp.dataset.auto = '1'; }
  }
  corpo.addEventListener('input', somar); corpo.addEventListener('change', somar);
  corpo.addEventListener('click', function (e) {
    if (!e.target.classList.contains('remover-item')) return;
    var linhas = corpo.querySelectorAll('tr.item');
    var tr = e.target.closest('tr');
    if (linhas.length > 1) tr.remove(); else tr.querySelectorAll('input,select').forEach(function (i) { i.value = ''; });
    somar();
  });
  document.getElementById('add-item').addEventListener('click', function () {
    var novo = corpo.querySelector('tr.item').cloneNode(true);
    novo.querySelectorAll('input,select').forEach(function (i) { i.value = ''; });
    corpo.appendChild(novo); novo.querySelector('select').focus();
  });
  somar();
})();

// chamado: a caixa Detalhes acompanha o que é escolhido na caixa Atualizar (antes mesmo de salvar)
(function () {
  var f = document.getElementById('form-atualizar');
  if (!f) return;
  ['status', 'prioridade', 'responsavel_id', 'contrato_id'].forEach(function (nome) {
    var sel = f.elements[nome], dd = document.querySelector('[data-espelho="' + nome + '"]');
    if (!sel || !dd) return;
    var original = sel.value;
    sel.addEventListener('change', function () {
      var op = sel.options[sel.selectedIndex];
      dd.textContent = op && op.value ? op.text : '—';
      if (sel.value !== original) {
        var s = document.createElement('span'); s.className = 'selo warn'; s.textContent = 'não salvo';
        dd.appendChild(document.createTextNode(' ')); dd.appendChild(s);
      }
    });
  });
})();

// respostas prontas no chamado e sugestões da base de conhecimento ao abrir chamado no portal
(function () {
  var sel = document.getElementById('resposta-pronta'), txt = document.getElementById('texto-resposta');
  if (sel && txt) sel.addEventListener('change', function () {
    var op = sel.options[sel.selectedIndex];
    if (!op.value) return;
    txt.value = (txt.value ? txt.value.replace(/\s+$/, '') + '\n\n' : '') + op.dataset.texto;
    txt.focus(); sel.value = '';
  });
  var t = document.getElementById('titulo-chamado'), box = document.getElementById('sugestoes-kb');
  if (!t || !box || !window.fetch) return;
  var espera;
  t.addEventListener('input', function () {
    clearTimeout(espera);
    espera = setTimeout(function () {
      if (t.value.trim().length < 4) { box.hidden = true; return; }
      fetch(t.dataset.sugestoes + '?q=' + encodeURIComponent(t.value), {credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (itens) {
          var ul = box.querySelector('ul'); ul.innerHTML = '';
          itens.forEach(function (i) {
            var li = document.createElement('li'), a = document.createElement('a');
            a.href = i.url; a.target = '_blank'; a.textContent = i.titulo; li.appendChild(a); ul.appendChild(li);
          });
          box.hidden = !itens.length;
        }).catch(function () {});
    }, 350);
  });
})();
