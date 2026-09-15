import asyncio
import re
import logging
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

ML_ITEMS_URL = "https://api.mercadolibre.com/items"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

# Teto de fotos por anuncio quando o ML nao souber informar o da categoria.
# 12 e o limite padrao do Mercado Livre (`settings.max_pictures_per_item` em
# GET /categories/{id}) e o valor observado em todas as categorias usadas por
# este projeto; e um teto DEFENSIVO, nao uma meta: o pipeline normal produz 6
# a 8 imagens. Existe porque a lista de fotos aprovadas nunca foi limitada em
# lugar nenhum — bastaria uma aprovacao em massa mal filtrada, ou uma segunda
# passada do pipeline, para o payload passar do limite e o ML recusar o item
# inteiro com um erro de validacao.
ML_MAX_PICTURES_FALLBACK = 12


def _exige_family_name(texto_erro: str) -> bool:
    """True quando o ML recusou o item por falta de `family_name`.

    A resposta vem como:
        body.required_fields | The body does not contains some or none of the
        following properties [family_name]
    """
    return "family_name" in (texto_erro or "")


class MLValidationError(Exception):
    """Erro de validação retornado pela API do ML (HTTP 400). Não deve ser retentado."""


class SellerDisconnectedError(RuntimeError):
    """Conta ML desconectada ou sem token: nao ha o que usar nem renovar.

    RuntimeError de proposito: os workers ja tratam RuntimeError como falha
    do listing (sem retry infinito), e a mensagem nomeia a conta para o
    operador saber onde reconectar.
    """


async def get_valid_access_token(seller, db) -> str:
    # Guarda ANTES de qualquer decrypt: desde b8e2d4f6a1c3 os tokens sao
    # anulaveis (conta desconectada). Sem isto o Fernet estouraria com
    # InvalidToken dentro de um worker — sintoma longe da causa.
    if not seller.is_active or not seller.access_token_enc or not seller.refresh_token_enc:
        raise SellerDisconnectedError(
            f"Conta ML {seller.ml_nickname!r} desconectada: reconecte em Contas antes de continuar."
        )

    now = datetime.now(timezone.utc)
    expires_at = seller.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at > now:
        return decrypt_value(seller.access_token_enc)

    settings = get_settings()
    refresh_token = decrypt_value(seller.refresh_token_enc)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.ml_app_id,
                "client_secret": settings.ml_client_secret,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Falha ao renovar token ML: {resp.text[:300]}")

    data = resp.json()
    from datetime import timedelta
    seller.access_token_enc = encrypt_value(data["access_token"])
    seller.refresh_token_enc = encrypt_value(data["refresh_token"])
    seller.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    await db.commit()
    return data["access_token"]


class PublishService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def activate_listing(self, listing, seller) -> None:
        token = await get_valid_access_token(seller, self.db)
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"https://api.mercadolibre.com/items/{listing.mlb_id}",
                json={"status": "active"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
        if response.status_code not in (200, 204):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Erro ao ativar anúncio no ML: {response.text}",
            )
        listing.status = "published"
        await self.db.commit()

    async def publish(
        self,
        listing,
        attributes: list,
        images: list,
        description_html: str | None,
        access_token: str,
    ) -> tuple[str, str]:
        attrs_payload = [
            {"id": a.attribute_id, "value_id": a.value_id, "value_name": a.value_name}
            if a.value_id
            else {"id": a.attribute_id, "value_name": a.value_name}
            for a in attributes
            if a.value_name
        ]

        # Teto de fotos: pergunta ao ML o limite DA CATEGORIA e cai no
        # `ML_MAX_PICTURES_FALLBACK` se a consulta nao responder. A ordenacao
        # por `sort_order` vem antes do corte de proposito — a capa (0) e as
        # fotos individuais tem os menores sort_order, entao o que eventualmente
        # sobra de fora e sempre o material acessorio (cards, candidatos), nunca
        # a capa.
        from app.services.category_service import get_category_max_pictures

        max_pictures = (
            await get_category_max_pictures(listing.ml_category_id)
        ) or ML_MAX_PICTURES_FALLBACK

        pics_payload = [
            {"id": img.ml_picture_id}
            for img in sorted(images, key=lambda x: x.sort_order)
            if img.approved and img.ml_picture_id
        ]
        if len(pics_payload) > max_pictures:
            logger.warning(
                "publish_pics_truncated listing_id=%s total=%s limite=%s",
                listing.id,
                len(pics_payload),
                max_pictures,
            )
            pics_payload = pics_payload[:max_pictures]
        if not pics_payload:
            raise RuntimeError("Nenhuma imagem aprovada encontrada para publicação")

        body = {
            "title": listing.selected_title,
            "category_id": listing.ml_category_id,
            "price": float(listing.price),
            "currency_id": "BRL",
            "available_quantity": listing.stock_quantity,
            "buying_mode": "buy_it_now",
            "condition": listing.condition,
            "listing_type_id": listing.listing_type_id,
            "pictures": pics_payload,
            "attributes": attrs_payload,
            "status": "active",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                ML_ITEMS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json=body,
            )

            # Modo catalogo: algumas categorias recusam `title` e exigem
            # `family_name` no lugar dele. Os dois sao MUTUAMENTE EXCLUSIVOS —
            # mandar ambos devolve "The fields [title] are invalid".
            #
            # A decisao de qual usar vem da RESPOSTA do ML, nao de um campo da
            # categoria. Foi tentado: `settings.catalog_domain` existe em
            # TODAS as categorias testadas (perfumes, desodorantes, celulares,
            # perfume pet), entao gatear nele mandaria todo anuncio para o modo
            # catalogo. Nenhum outro campo de `settings` discrimina. Perguntar
            # e mais barato e mais honesto que adivinhar por um proxy errado.
            if resp.status_code == 400 and _exige_family_name(resp.text):
                logger.info(
                    "publish_catalog_mode listing_id=%s categoria=%s "
                    "motivo=ml_exigiu_family_name",
                    listing.id,
                    listing.ml_category_id,
                )
                body_catalogo = {k: v for k, v in body.items() if k != "title"}
                # O family_name sai do titulo que o seller aprovou. Nao ha
                # invencao aqui: e o mesmo texto que iria no `title`.
                body_catalogo["family_name"] = listing.selected_title
                resp = await client.post(
                    ML_ITEMS_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=body_catalogo,
                )

        if resp.status_code == 400:
            error_text = resp.text[:1200]
            try:
                err_data = resp.json()
                causes = err_data.get("cause", [])
                msgs = [c["message"] for c in causes if c.get("message")]
                if msgs:
                    error_text = "ML rejeitou o anúncio: " + " | ".join(msgs)
            except Exception:
                pass
            raise MLValidationError(error_text)
        if resp.status_code >= 400:
            raise RuntimeError(f"ML criar item {resp.status_code}: {resp.text[:600]}")

        item_data = resp.json()
        item_id = item_data["id"]

        estado_final = await self._aguardar_validacao(item_id, item_data, access_token)

        if description_html:
            await self._post_description(item_id, description_html, access_token)

        return item_id, estado_final

    async def _aguardar_validacao(self, item_id: str, item_data: dict, access_token: str) -> str:
        """Espera a validação assíncrona das fotos terminar e devolve o
        status final do item no ML ('active', 'paused', 'under_review'...).

        NÃO força status nenhum. O passo 0 de feat/publicar-ativo provou que
        o ML ignora o status pedido na criação e decide sozinho ao fim da
        validação — o PUT que existia aqui só desfazia essa decisão.
        Se a validação não terminar dentro da janela (5 × 2 s), devolve o
        último estado visto: é foto do momento, não destino final.
        """
        status_value = item_data.get("status") or ""
        sub_status = item_data.get("sub_status") or []

        for _ in range(5):
            if "picture_download_pending" not in sub_status:
                break
            await asyncio.sleep(2)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{ML_ITEMS_URL}/{item_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if resp.status_code != 200:
                logger.warning("Falha ao checar status do item %s: %s", item_id, resp.text[:200])
                break
            item_data = resp.json()
            status_value = item_data.get("status") or ""
            sub_status = item_data.get("sub_status") or []

        return status_value

    async def _post_description(self, item_id: str, html: str, access_token: str) -> None:
        plain = re.sub(r"<[^>]+>", " ", html)
        plain = re.sub(r" {2,}", " ", plain).strip()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{ML_ITEMS_URL}/{item_id}/description",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"plain_text": plain},
            )
        if resp.status_code >= 400:
            logger.warning("Falha ao postar descrição para %s: %s", item_id, resp.text[:200])


async def fetch_item(item_id: str, access_token: str) -> dict:
    """Estado atual do item no ML. GET AUTENTICADO.

    A chamada publica a `/items/{id}` passou a devolver 403 — conferir o
    estado real do anuncio exige o token do seller. Existe como funcao propria
    porque toda promocao precisa LER o item antes de escrever: os
    `ml_picture_id` que estao no ar sao a verdade, nao o que o banco acha que
    esta la.
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.mercadolibre.com/items/{item_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro ao consultar item no ML: {response.text}",
        )
    return response.json()


async def replace_item_pictures(
    item_id: str,
    picture_ids: list[str],
    access_token: str,
    must_keep: list[str] | None = None,
) -> None:
    """Substitui a lista de fotos de um item JA publicado.

    O PUT de `pictures` no ML e SUBSTITUICAO TOTAL, nao merge: mandar 2 IDs
    num item de 8 fotos nao troca duas — deixa o anuncio com duas. Toda a
    validacao abaixo existe por causa disso, porque o erro so aparece no
    anuncio ao vivo, depois de ja ter apagado foto de verdade.

    - lista vazia -> `ValueError` (apagaria todas as fotos);
    - ID repetido -> `ValueError` (sintoma de lista mal montada, e publicaria
      a mesma foto duas vezes ocupando o lugar de outra);
    - `must_keep` -> `ValueError` nomeando o que sumiu. E o guard de "nenhum
      ID que deve permanecer pode faltar", conferido AQUI e nao so no call
      site, para que qualquer promocao futura o herde de graca.

    A ordem da lista e a ordem das fotos no anuncio: `picture_ids[0]` vira a
    capa.
    """
    if not picture_ids:
        raise ValueError(
            "lista de fotos vazia: o PUT e substituicao total e apagaria todas as fotos do anuncio"
        )

    duplicados = {p for p in picture_ids if picture_ids.count(p) > 1}
    if duplicados:
        raise ValueError(f"ml_picture_id repetido na lista: {sorted(duplicados)}")

    if must_keep:
        faltando = [p for p in must_keep if p not in picture_ids]
        if faltando:
            raise ValueError(
                f"lista incompleta — estes ml_picture_id sumiriam do anuncio: {faltando}"
            )

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"https://api.mercadolibre.com/items/{item_id}",
            json={"pictures": [{"id": p} for p in picture_ids]},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
    if response.status_code not in (200, 204):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erro ao atualizar fotos no ML: {response.text}",
        )
    logger.info(
        "replace_pictures item_id=%s total=%s", item_id, len(picture_ids)
    )
