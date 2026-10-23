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
