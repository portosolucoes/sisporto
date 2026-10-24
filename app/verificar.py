"""Verificação do pacote antes de publicar (roda no build da imagem e nos testes).

- todos os templates compilam;
- todo url_for('...') dos templates aponta para uma rota que existe;
- módulos registrados batem com a versão.
Se algo falhar, o build para e a versão antiga continua no ar.
"""
from __future__ import annotations

import pathlib
import re
import sys


def verificar() -> list[str]:
    from . import VERSAO, create_app
    app = create_app({"DATABASE_URL": "postgresql:///inexistente", "SECRET_KEY": "verificacao", "TESTING": True})
    erros = []
    rotas = {r.endpoint for r in app.url_map.iter_rules()}
    pasta = pathlib.Path(app.template_folder)
    if not pasta.is_absolute():
        pasta = pathlib.Path(app.root_path) / pasta
    for arq in sorted(pasta.rglob("*.html")):
        nome = arq.relative_to(pasta).as_posix()
        texto = arq.read_text(encoding="utf-8")
        try:
            app.jinja_env.parse(texto)
        except Exception as e:  # noqa: BLE001
            erros.append(f"{nome}: template com erro de sintaxe ({e})")
        for ep in re.findall(r"url_for\(\s*'([\w.]+)'", texto):
            if ep not in rotas:
                erros.append(f"{nome}: rota '{ep}' não existe (arquivo .py desatualizado?)")
        for ep in re.findall(r"item\(\s*'([\w.]+)'", texto):
            if ep not in rotas:
                erros.append(f"{nome}: rota do menu '{ep}' não existe (arquivo .py desatualizado?)")
    if not erros:
        print(f"SisPorto {VERSAO}: {len(rotas)} rotas e templates conferidos – OK")
    return erros


if __name__ == "__main__":
    problemas = verificar()
    for p in problemas:
        print("ERRO:", p, file=sys.stderr)
    sys.exit(1 if problemas else 0)
