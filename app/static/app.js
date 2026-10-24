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

// Cronômetro do chamado: conta na tela e pausa sozinho ao atingir o saldo
(function () {
  var el = document.querySelector('.cronometro');
  if (!el) return;
  var seg = Number(el.dataset.segundos || 0), rodando = el.dataset.rodando === '1';
  var limite = el.dataset.limite === '' ? null : Number(el.dataset.limite);
  var inicio = Date.now(), enviado = false;
  function fmt(s) { var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return [h, m, x].map(function (n) { return String(n).padStart(2, '0'); }).join(':'); }
  function tick() {
    var atual = rodando ? seg + Math.floor((Date.now() - inicio) / 1000) : seg;
    if (limite !== null && atual >= limite) {
      atual = limite; el.classList.add('esgotado');
      var f = document.getElementById('form-pausar');
      if (rodando && f && !enviado) { enviado = true; f.submit(); }
    }
    el.textContent = fmt(atual);
  }
  tick(); if (rodando) setInterval(tick, 1000);
})();
