# Entrega das imagens no bucket próprio do seller — ideia pausada

**Status:** pausada e removida do código em 2026-09-10. Este documento existe
para a ideia não precisar ser redescoberta do zero quando um cliente pedir.

## O que era

RF7 do design do pipeline image-to-image (`2026-07-08-pipeline-imagens-image-to-image-design.md`):
depois que o anúncio era publicado e ganhava `mlb_id`, o backend gravava as
fotos finais do anúncio **no bucket do próprio seller**, em
`anuncios/{mlb_id}-{n}.jpg`, usando credenciais S3 por seller guardadas
cifradas (Fernet) em `SellerImageConfig.write_*`, configuráveis por
`PUT /sellers/image-config` e pela tela de configurações. Best-effort: nunca
bloqueava nem revertia a publicação; sucesso/falha ficavam em
`ListingImage.r2_write_status` e `url_r2`.

A motivação era de produto: "cada seller com sua própria conta Cloudflare",
com as fotos publicadas como ativo do cliente, fora da infraestrutura da 360.

## Por que foi pausada

- Nunca teve demanda real. Nenhum seller configurou credencial de escrita —
  nem o CAFE085, o único em operação.
- O serviço rodou nas duas publicações reais (SKUs 37 e 38) e as 11 linhas
  resultantes ficaram todas em `r2_write_status = skipped_no_config`. Zero
  linhas com `url_r2`. Código construído, ligado, testado e jamais alimentado.
- Enquanto isso, o sistema ganhou um bucket **interno** de ativos
  (`r2-mktp-img-ia`, `services/r2_asset_service.py`): todo candidato gerado por
  IA vai para lá na geração, aprovado ou não, e é a fonte das variantes. Esse
  bucket resolve o problema de *armazenamento de trabalho*; a entrega ao
  cliente é outro problema, e ficou sem dono.

Pelo critério já aplicado ao motor de imagem antigo, código dormente atrás de
uma bifurcação que ninguém alcança sai inteiro: serviço, colunas, campos de
API e de tela, testes.

## Correção de desenho já identificada, para quando for retomada

O desenho original **baixava as fotos de novo do CDN do Mercado Livre** para
gravá-las no bucket do seller. Isso entrega ao cliente uma versão
**recomprimida e possivelmente recortada** pelo ML, não o arquivo que geramos.

Quando a entrega for retomada, a fonte tem de ser o **`asset_key` interno**:
o objeto em `r2-mktp-img-ia` é o byte exato que subiu ao ML (1200×1200,
tratado). A operação vira um `CopyObject`/`GetObject`+`PutObject` entre buckets,
sem passar pelo CDN do ML, e continua acontecendo **na publicação** (o nome de
destino ainda precisa do `mlb_id`), só para as imagens aprovadas.

Esqueleto sugerido:

- credenciais por seller voltam como estavam (cifradas, por API e tela);
- `publish_tasks`, depois de `listing.mlb_id`: para cada `ListingImage`
  aprovada com `asset_key`, copiar do bucket interno para
  `{bucket_do_seller}/anuncios/{mlb_id}-{n}.jpg`;
- best-effort, com status por imagem, exatamente como antes.

## O que saiu do código (2026-09-10)

`services/r2_write_service.py`; colunas `write_bucket_name`,
`write_endpoint_url`, `write_access_key_id_enc`, `write_secret_access_key_enc`
de `seller_image_configs`; `r2_write_status` e `url_r2` de `listing_images`
(migration `8e3a5c1d7f92`, com backup das duas tabelas antes); campos
correspondentes em `PUT /sellers/image-config`, nos schemas, nos types e na
tela de configurações do frontend; os testes do serviço e da configuração.
