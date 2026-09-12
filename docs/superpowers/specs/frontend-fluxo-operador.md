# Fluxo do operador — desenho do frontend (bloco B)

Status: desenho aprovado por Daniel em 2026-09-11. A fila de trabalho (item 1 da ordem de construção) foi construída em 2026-09-12; o restante antecede as tarefas de implementação. Onde diz PENDENTE, a decisão ainda não foi tomada.

## Princípio

O produto é a tela de revisão. A medida de sucesso é tempo humano por anúncio revisado com segurança, não número de cliques. Os cliques que existem — aprovar imagens e publicar — são decisões deliberadas e não serão removidos.

Operadores: equipe interna treinada (Daniel, Gabriel, depois um terceiro). Telas densas, com atalho de teclado. Sem tutorial, sem linguagem de iniciante.

## Telas

### 1. Fila de trabalho (construída; substituiu o PipelineBoard, já removido)

Tela principal de listings. Construída em três tarefas, todas em `master` desde 2026-09-12: `e176fb2` (camada de API), `41c9d6d` (tela), `2349bb5` (seleção e ações em massa). O quadro antigo — `PipelineBoard.tsx`, `ListingCard.tsx` e a rota `/listings/board` — foi **removido** em `2349bb5`; não existe mais nada dele para procurar.

- Barra de resumo no topo: contagem por status vinda de `GET /listings/status-counts`, nunca do que está carregado. Clicar num número filtra a fila.
- Lista densa abaixo: linhas de altura fixa, paginação de 50, filtro por status (aceita vários) e busca por texto — `GET /listings?status=a&status=b&search=x&page_size=50`.
- Colunas: SKU, título (ou descrição de origem quando não há título), categoria, status, o que espera ação.
- Cor só para o que exige atenção (falha, espera). O resto neutro.
- Seleção múltipla com ação em massa. "Selecionar tudo deste filtro" exige endpoint de ação em massa por filtro no servidor — PENDENTE, não existe hoje.
- Não atualiza sozinha: recarrega ao entrar, ao voltar da revisão e num botão explícito. O refetch automático de 8s do quadro antigo movia a linha sob o cursor.
- Anúncio incompleto (menos de 5 posições) recebe marca visível antes de qualquer aprovação em massa.

Decidido durante a construção (não estava no desenho original; hoje é comportamento):

- Abre com o filtro **"Esperando você"** aplicado (decisão de produto, Daniel, 2026-09-12). "Todos" limpa.
- Clique na linha vai direto à etapa que espera ação, pela tabela de `lib/listing-destination.ts` (títulos, atributos, imagens, preview; qualquer outro status cai no detalhe). O **SKU** é sempre atalho para o detalhe, em qualquer status.
- Ações em massa derivam do **status dos selecionados** (`lib/bulk-actions.ts`), não de coluna. **Seleção mista não tem ação**: a barra pede anúncios no mesmo estágio, porque cada endpoint recusa item fora do estado esperado com "estado inválido".
- **Publicar exige confirmação** com contagem e lista de SKUs: é a única ação irreversível (manda anúncio ao ar no ML). As outras continuam com um clique.
- Erro técnico (`SQL`, `Traceback`, `sqlalchemy`, ou texto longo) **nunca** vai para a tela: vira frase genérica e o original vai para o console (`sanitizeBulkError`). Erro de negócio ("estado inválido", "nenhuma imagem aprovável") passa como veio, por SKU.
- A seleção sobrevive à paginação e ao filtro (estado próprio, `Map` por id) e guarda o status do momento em que a linha foi marcada. Se um worker mudar o status antes do clique, o backend recusa o item com "estado inválido" — comportamento correto: o backend é a fonte da verdade, a seleção é só uma foto.
- `ListingSummary` passou a trazer `sku_description`, `ml_category_id` e `approved_image_count` (`d6d97bf`), e as FKs `listing_id` de imagens, títulos e jobs ganharam índice (`c2bc490`, migração `f7b3e9c1d2a5`) porque a contagem de aprovadas fazia Seq Scan por linha da listagem.

### 2. Revisão de imagens (tela cheia)

Aberta a partir de uma linha da fila. Substitui a galeria atual, que foi feita para "selecione as imagens que quiser" e não conhece posições.

- As 5 posições identificadas pelo `kind` do `ImageOut`: cover_ai (ou cover_deterministic), presentation_ai, benefits_ai, detail_ai, specs_ai.
- Candidatas (`is_candidate`, calculado no backend) nunca aparecem na galeria principal. Quando existe candidata de capa ou de ficha, aparece um par lado a lado para o humano escolher, com a ação de promover.
- Posição reprovada no QA mostra `validation_error`, não um quadro vazio.
- Caminho completo da categoria visível na tela: é o primeiro ponto em que um erro de título/categoria pode ser pego por um humano.
- Cronômetro: mede o tempo da revisão e envia em `review_seconds` na aprovação. Hoje o frontend não envia, e todo evento nasce com tempo nulo.
- A ordem em que os ids são enviados é a ordem publicada no ML (ver CLAUDE.md, "A aprovação individual renumera"). A tela precisa enviar na ordem desejada.
- Navegação por teclado entre anúncios, sem voltar à fila a cada um.
- Botões de retomada, hoje sem nenhuma tela: `pipeline/resume_raw_photos` e `pipeline/resume_ai_engine`. A mensagem de fotos ausentes manda usar "Verificar fotos agora", e esse botão não existe.
- A orientação de standby é montada a partir do status, nunca do `error_message` gravado: o texto gravado é uma foto do momento em que o anúncio parou e não acompanha correções.

### 3. Atributos

- Mostrar só `is_editable` (calculado no backend). Em MLB7863 isso é 13 de 80.
- `VEHICLE_TYPE` e outros com a tag `fixed`: mostrar preenchido e sem edição — valor cravado pela categoria, obrigatório mas sem escolha.
- Atributo sem tags gravadas (linha anterior à migração `e5f9c3b7a2d4`) é editável: na dúvida, mostrar. Anúncio antigo exibirá os 80.

### 4. Importação (bloco C, referência aqui)

Checar as fotos brutas no momento do upload, não na etapa de imagem: hoje o anúncio gasta título e categoria antes de descobrir que faltam fotos. Orientar o preenchimento da planilha (a palavra de tipo na descrição decide a categoria; modelo da peça e veículo disputam a mesma coluna).

### 5. Configurações

O card de bucket afirma duas coisas falsas: que sem configuração o pipeline gera imagem a partir de texto (removido) e a regra antiga de `.jpg`. Reescrever.

## Regras que o frontend consome e NÃO reimplementa

- `is_candidate` (`ListingImage.is_candidate`): candidata é posição >= 90, nunca kind.
- `is_editable` (`ListingAttribute.is_editable`): esconder só `hidden` ou `read_only`.

Duplicar qualquer uma no frontend cria duas fontes para a mesma regra. Foi exatamente assim que nasceu o bug da aprovação em massa (kind × posição).

## Erros

Erro de servidor nunca chega cru à tela. O caso conhecido: violação de índice único aparecia como texto de SQL no aviso.

## Ordem de construção

1. ~~Fila de trabalho (porta de entrada; a revisão abre a partir dela)~~ **Feito em 2026-09-12**: `e176fb2` (camada de API), `41c9d6d` (tela), `2349bb5` (seleção e ações). Quadro removido.
2. Revisão de imagens
3. Atributos
4. Bloco C (entrada)
5. Bloco D (pôr de pé: Dockerfile, compose, Nginx com HTTPS, usuário por operador, limpeza final)

## Pendências conhecidas

- Endpoint de ação em massa por filtro (para "selecionar tudo deste filtro").
- Promover capa/ficha não gera evento de revisão; a coluna `action` já comporta.
- Lista de status duplicada: `LISTING_STATUSES` no backend e `ListingStatus` no frontend, sem nada que force sincronia.
- 8 rotas `/listings/bulk/*` ausentes da lista de endpoints do CLAUDE.md.
- **Número de posições escrito à mão no frontend.** A marca de "anúncio incompleto" compara `approved_image_count` com **5**, fixo em `WorkQueue.tsx` (`FULL_GALLERY`). O SKU 37, publicado antes do esquema de 5 posições, tem 8 aprovadas — prova de que o número não é universal. Quando existir perfil com outra quantidade de posições, o backend deve informar quantas o perfil espera, e a fila comparar com isso. É a mesma família do bug da aprovação em massa: regra do presente escrita longe da fonte da verdade.
- **"Estado inválido" pode ficar obscuro em uso real.** Quando o worker avança um anúncio entre a seleção e o clique, o operador lê "estado inválido" sem saber que o anúncio simplesmente andou sozinho. Avaliar um texto mais claro **depois** de Daniel e Gabriel usarem de verdade — decisão que pede dado de uso, não palpite.
- **"Selecionar tudo deste filtro"** continua fora: exige endpoint de ação em massa por filtro no servidor. Hoje a seleção cobre página por página, e sobrevive à paginação.

## Nota de ambiente

Depois de remover uma rota do Next, `npx tsc --noEmit` pode acusar erro fantasma vindo de `.next/types` do build anterior. Apagar `.next` e repetir. Aconteceu na revisão de `2349bb5`.
