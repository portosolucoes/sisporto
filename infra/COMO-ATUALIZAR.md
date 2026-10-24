# Como atualizar o SisPorto

## Jeito automático (recomendado) – depois da configuração única abaixo
1. Receba o arquivo **sisporto-vX.Y.Z.zip** (ele chega na pasta SisPorto do seu computador).
2. No GitHub, abra o repositório **portosolucoes/sisporto** → **Add file → Upload files**.
3. Arraste **só o zip** e clique em **Commit changes**.
4. Pronto. Em **Actions** acompanhe: o GitHub confere o pacote (impressão digital de cada arquivo), aplica os
   arquivos, apaga o zip, roda os testes num PostgreSQL de verdade e publica no Google Cloud (~8 min).
   - Se algo falhar, **nada é publicado**, a versão anterior continua no ar e o GitHub manda e-mail.
   - O número da versão aparece no rodapé do menu do sistema.

## Configuração única (≈5 min, uma vez só)
1. **Cloud Shell**: envie o zip (⋮ → Upload) e rode:
   ```
   cd ~/sisporto && unzip -o ~/sisporto-v1.6.1.zip && bash infra/gcp/configurar-github.sh
   ```
   Isso autoriza só o repositório portosolucoes/sisporto (branch main) a publicar – sem chave ou senha guardada.
2. **GitHub**: Add file → Upload files → arraste a pasta **.github** (de dentro do zip extraído) → Commit.
   (No Windows, se a pasta não aparecer, ative "Itens ocultos" no Explorador de Arquivos.)
3. Depois disso, use sempre o jeito automático.

## Jeito manual (se o GitHub estiver fora)
Cloud Shell: envie o zip (⋮ → Upload) e rode
```
cd ~/sisporto && unzip -o ~/sisporto-vX.Y.Z.zip && bash infra/gcp/publicar.sh
```
O script confere o pacote antes de publicar e para se algum arquivo estiver diferente.

## O que roda sozinho a cada versão
- Conferência dos arquivos (MANIFESTO.sha256) – evita versão "misturada" como aconteceu na 1.6.0.
- Verificação de rotas e telas no build da imagem.
- Testes automáticos (mais de 70) com PostgreSQL 16.
- Publicação no Cloud Run e atualização do banco (migrações).
- Senhas e dados não são alterados.
