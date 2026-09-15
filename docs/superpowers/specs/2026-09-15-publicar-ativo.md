# Publicar ativo — spec (passo 0 aprovado em 2026-09-15)

## Contexto

A leitura do anúncio 31 confirmou a hipótese 1: o ML **ignorou** o
`"status": "paused"` enviado na criação e devolveu o item em outro estado;
290 ms depois o nosso `PUT` o empurrou para pausado. A mudança não cria
comportamento novo — **para de desfazer** o padrão do ML.

## Escopo aprovado

1. **Criar o item com `"status": "active"`** no payload de `POST /items`
   (nos dois modos: `title` e `family_name`).
2. **`_ensure_paused` vira `_aguardar_validacao`**: mantém a espera pelo
   `sub_status` limpar (`picture_download_pending`), **remove** o `PUT` que
   força pausado, e passa a **reportar o estado final** do item — incluindo
   `under_review`. Hoje o sistema marca como publicado sem saber se o ML
   aceitou as fotos; foto recusada manda o anúncio para revisão lá e ninguém
   fica sabendo aqui.
3. **`POST /{listing_id}/activate` fica**, virando "reativar anúncio pausado
   no ML", e **ganha botão na tela**. O ML pausa por conta própria (falta de
   estoque, moderação) e hoje não há caminho de volta.
4. **Rótulo `published_paused`** deixa de ser fluxo normal e passa a ser
   anomalia. Revisar o texto.
5. **Textos de confirmação** nos dois lugares mapeados no passo 0 — a barra
   de ação em massa (`BulkActionsBar`) e a prévia individual
   (`ListingPreview`) — dizendo que o anúncio vai ao ar **imediatamente** e
   passa a receber visitas e vendas.

## Invariante obrigatória

**Nenhum anúncio existente pode ser ativado como efeito desta mudança.**
Satisfeita por construção (só a criação de item novo muda; `activate` continua
exigindo POST explícito em `published_paused`); coberta por teste. Sem
migração de dados, sem varredura retroativa. Os anúncios 31, 37 e 38
continuam como estão.

## Decisões tomadas na elaboração do plano (rulings do controlador)

- **`under_review` ganha status local próprio: `published_under_review`.**
  "Grava no anúncio" e "mostra na fila" com o custo mínimo: um item a mais em
  `LISTING_STATUSES` (17), rótulo "Em análise no ML", grupo "Concluído" na
  fila. Sem migração (a coluna é varchar sem constraint). O que fica de fora
  — e vira tarefa própria junto com a pendência de visibilidade de moderação
  — é acompanhar a moderação DEPOIS da publicação (polling de
  `last_moderation`, infrações, notificação).
- **Mapa estado ML → status local, no worker:** `active` → `published`;
  `under_review` → `published_under_review`; **qualquer outra coisa**
  (`paused` inclusive, e estado desconhecido) → `published_paused` com
  `logger.warning`. `published_paused` passa a ser o balde de anomalia.
- **`published_paused` muda de grupo na fila: `done` → `waiting`**
  ("Esperando você"). Anomalia com botão de reativar é ação pendente do
  operador, não trabalho concluído. `published_under_review` fica em `done`
  (saiu das nossas mãos; o ML decide).
- **Limitação conhecida e aceita:** a espera de `_aguardar_validacao` continua
  em 5 tentativas × 2 s. Se o `picture_download_pending` não limpar nesse
  intervalo, o estado reportado é o último visto (tipicamente `paused`) e o
  anúncio fica `published_paused` mesmo que o ML o ative minutos depois — o
  operador confere no ML e reativa; o rótulo local não é destino final errado,
  é foto do momento. Alongar a espera não entrou no escopo.
- **Limitação conhecida (2):** o `estado_ml` gravado é a foto tirada ANTES do
  `POST /description` (a ordem interna de `publish()` não mudou); se o envio
  da descrição algum dia mudar o estado do item no ML, o status local nasce
  desatualizado. Registrado na revisão da Task 2; sem mudança de código.
- **Branch a partir do HEAD atual de `master` (`c816332`)**, não do `44b1c92`
  citado no pedido: o commit a mais é só documentação (pendência da predição
  de categoria) e já está em master.

## Linhas de base

Suíte em `c816332`: **671 passed / 88 skipped** sem `TEST_DATABASE_URL`;
**759 passed** com ela. Fim: revisão do branch inteiro; publicar a branch
`feat/publicar-ativo` **sem merge e sem deploy**.

## Notas de deploy e pendências (da revisão final, 2026-09-15)

- **Antes do deploy do frontend:** os `published_paused` legados (anúncios
  31, 37 e 38, e o que mais houver) passam a aparecer em "Esperando você"
  com o botão "Reativar anúncio" — mas foram pausados de propósito no regime
  antigo, e o 37 está `under_review` no ML por exigência de catálogo (reativar
  não resolve). Rodar `SELECT id, sku_external_id, mlb_id FROM listings WHERE
  status = 'published_paused'` em produção e decidir, linha a linha com o
  Daniel, o que é "reativar" e o que é "deixar quieto".
- **Pendência (tarefa própria, junto com visibilidade de moderação):**
  `published_under_review` não tem NENHUM comportamento além de existir —
  ninguém volta a perguntar ao ML o que aconteceu; um anúncio pode ficar ali
  para sempre. O mesmo vale para `activate_listing`, que grava `published`
  sem ler a resposta do PUT (se o ML re-pausar na hora por falta de estoque,
  o rótulo local fica errado até alguém notar) e repassa o texto cru do ML
  no 502.
- **Cobertura deixada de fora de propósito (triagem da revisão final):** o
  limite de 5 iterações de `_aguardar_validacao` não tem teste (escrever com
  `side_effect` limitado, nunca mock constante — senão trava em vez de
  falhar) e o body do modo catálogo não crava `"status": "active"` em teste
  (carregado por construção do dict).
