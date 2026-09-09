import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.models.listing import Listing
from app.models.listing_attribute import ListingAttribute
from app.models.seller import Seller

logger = logging.getLogger(__name__)

_ML_API = "https://api.mercadolibre.com"

# Atributos que o sistema pré-preenche automaticamente (usados para definir source="seller")
_PREFILL_KEYS = {"BRAND", "GTIN", "ITEM_CONDITION", "SELLER_SKU", "MODEL",
                 "SELLER_PACKAGE_WEIGHT", "SELLER_PACKAGE_LENGTH",
                 "SELLER_PACKAGE_WIDTH", "SELLER_PACKAGE_HEIGHT"}

# Categorias-raiz do site MLB que exigem fundo branco puro na foto de capa.
# A Central de Aprendizagem do ML descreve a regra por agrupamento comercial
# ("Tecnologia", "Beleza", "Saúde", "Supermercado"), que não são categorias-raiz
# da árvore — cada agrupamento foi mapeado para os IDs de raiz reais abaixo,
# confirmados um a um em GET /categories/{id} (path_from_root de tamanho 1).
_WHITE_BG_ROOT_CATEGORIES: dict[str, str] = {
    # Tecnologia
    "MLB1051": "Celulares e Telefones",
    "MLB1648": "Informática",
    "MLB1000": "Eletrônicos, Áudio e Vídeo",
    "MLB1039": "Câmeras e Acessórios",
    "MLB1144": "Games",
    "MLB5726": "Eletrodomésticos",
    # Beleza
    "MLB1246": "Beleza e Cuidado Pessoal",
    # Saúde
    "MLB264586": "Saúde",
    # Supermercado
    "MLB1403": "Alimentos e Bebidas",
}


async def get_root_category_id(category_id: str, token: str | None = None) -> str | None:
    """Primeiro item de `path_from_root` da categoria — o ID da raiz.

    `GET /categories/{id}` responde sem autenticação, então `token` é opcional.
    Retorna None se a categoria não existir ou o ML estiver indisponível: o
    chamador trata isso como "não sei", nunca como "exige branco".
    """
    if not category_id:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_ML_API}/categories/{category_id}", headers=headers)
        resp.raise_for_status()
        path = resp.json().get("path_from_root") or []
    except Exception:
        return None
    return path[0]["id"] if path else None


# Teto de sanidade para o valor que vem do ML. O limite real e 12 na
# esmagadora maioria das categorias, e o proprio ML documenta o campo como
# "por categoria" — mas aceitar qualquer inteiro positivo significa que uma
# resposta corrompida, um campo que mude de semantica ou um mock mal montado
# fariam a publicacao tentar subir centenas de fotos. Acima disto tratamos
# como "nao sei" e o chamador cai no teto fixo (`ML_MAX_PICTURES_FALLBACK`).
# 24 = o dobro do limite padrao: folga para uma categoria realmente mais
# generosa, sem espaco para um valor absurdo passar.
ML_MAX_PICTURES_SANITY_CAP = 24


async def get_category_max_pictures(category_id: str, token: str | None = None) -> int | None:
    """`settings.max_pictures_per_item` da categoria, ou None se nao der pra saber.

    O ML publica o limite de fotos por categoria em `GET /categories/{id}` —
    hoje 12 na esmagadora maioria, mas o proprio ML documenta que o valor e
    por categoria. Devolver None (categoria inexistente, ML fora do ar, campo
    ausente ou valor absurdo) significa "nao sei": quem chama aplica o teto
    de seguranca fixo em vez de publicar sem teto nenhum.
    """
    if not category_id:
        return None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{_ML_API}/categories/{category_id}", headers=headers)
        resp.raise_for_status()
        value = (resp.json().get("settings") or {}).get("max_pictures_per_item")
    except Exception:
        return None
    # `isinstance(True, int)` e True em Python — o teste de bool vem primeiro
    # para que `"max_pictures_per_item": true` nao vire um teto de 1 foto.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > ML_MAX_PICTURES_SANITY_CAP:
        return None
    return value


async def category_requires_white_background(
    category_id: str, token: str | None = None
) -> bool:
    """True se a categoria-raiz do anúncio exigir fundo branco puro na capa.

    Resolve pela raiz, não pelo nome da categoria-folha. Em caso de falha na
    consulta ao ML devolve False — a triagem de fundo branco é uma verificação
    extra, e reprovar imagem por indisponibilidade do ML travaria o pipeline.
    """
    root_id = await get_root_category_id(category_id, token)
    return root_id in _WHITE_BG_ROOT_CATEGORIES


class CategoryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def predict_and_save(self, listing: Listing, ean: str | None = None) -> None:
        from app.services.publish_service import get_valid_access_token
        result = await self.db.execute(select(Seller).where(Seller.id == listing.seller_id))
        seller = result.scalar_one()
        token = await get_valid_access_token(seller, self.db)

        category_id = await self._predict_category(listing.selected_title, token)
        listing.ml_category_id = category_id

        raw_attrs = await self._get_attributes(category_id, token)
        await self._save_attributes(listing, raw_attrs, ean=ean)

    async def _predict_category(self, title: str, token: str) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_ML_API}/sites/MLB/domain_discovery/search",
                params={"q": title},
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            raise ValueError(f"Nenhuma categoria ML encontrada para: {title!r}")
        return results[0]["category_id"]

    async def _get_attributes(self, category_id: str, token: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_ML_API}/categories/{category_id}/attributes",
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        return resp.json()

    async def _save_attributes(self, listing: Listing, raw_attrs: list[dict], ean: str | None = None) -> None:
        # Remove atributos de tentativas anteriores — garante idempotência em retries
        await self.db.execute(
            delete(ListingAttribute).where(ListingAttribute.listing_id == listing.id)
        )

        prefill: dict[str, str | None] = {
            "ITEM_CONDITION": "Novo" if listing.condition == "new" else "Usado",
        }

        # BRAND: só preenche se houver marca real (não placeholder "Sem marca")
        brand = (listing.sku_brand or "").strip()
        if brand and brand.lower() != "sem marca":
            prefill["BRAND"] = brand

        # GTIN: só preenche se o EAN for uma sequência numérica de comprimento válido
        if ean and ean.strip().isdigit() and len(ean.strip()) in (8, 12, 13, 14):
            prefill["GTIN"] = ean.strip()

        if listing.sku_external_id:
            prefill["SELLER_SKU"] = listing.sku_external_id

        model = (getattr(listing, "sku_model", None) or "").strip()
        if model:
            prefill["MODEL"] = model

        if listing.package_weight_kg is not None:
            from decimal import Decimal as _D
            weight_g = _D(str(listing.package_weight_kg)) * _D("1000")
            # ML espera valor + unidade: "120 g"
            prefill["SELLER_PACKAGE_WEIGHT"] = format(weight_g, 'f').rstrip("0").rstrip(".") + " g"

        if listing.package_length_cm is not None:
            prefill["SELLER_PACKAGE_LENGTH"] = f"{listing.package_length_cm} cm"
        if listing.package_width_cm is not None:
            prefill["SELLER_PACKAGE_WIDTH"] = f"{listing.package_width_cm} cm"
        if listing.package_height_cm is not None:
            prefill["SELLER_PACKAGE_HEIGHT"] = f"{listing.package_height_cm} cm"

        has_unfilled_required = False

        # `EMPTY_GTIN_REASON` e `GTIN` chegam do ML com a MESMA tag,
        # `conditional_required` (nenhum e `required`): o ML exige um OU o
        # outro. A API nao diz de que o "conditional" depende, entao a
        # dependencia e regra nossa — mas gateada pela tag: `required` puro
        # continua sendo obedecido. Sem isso, todo anuncio com EAN valido
        # parava em `pending_seller_attributes` por um atributo OCULTO que o
        # proprio ML nao pediria, e em lote nenhum SKU chegava as imagens.
        gtin_preenchido = bool(prefill.get("GTIN"))

        for attr in raw_attrs:
            attr_id: str = attr["id"]
            tags = attr.get("tags", {})
            condicional = bool(tags.get("conditional_required", False))
            if attr_id == "EMPTY_GTIN_REASON" and condicional and gtin_preenchido:
                condicional = False
            is_required: bool = bool(tags.get("required", False) or condicional)
            attr_type: str = attr.get("value_type", "string")
            allowed: list | None = attr.get("values") or None

            value_name: str | None = prefill.get(attr_id)
            value_id: str | None = None

            # `values` significa coisas diferentes conforme o `value_type`:
            #
            #   - `list`  -> ENUMERACAO fechada. Valor fora dela e recusado pelo
            #     ML na publicacao, entao descartar aqui e o certo.
            #   - `string` -> lista de SUGESTOES. O ML aceita texto livre e
            #     resolve o id por conta propria.
            #
            # Tratar os dois igual descartava marca legitima: BRAND em MLB6284
            # devolve 24 sugestoes, "Wepink" nao esta entre elas, e mesmo assim
            # o anuncio MLB5145387291 esta ATIVO nessa categoria com
            # `BRAND value_id='13065330' value_name='Wepink'` — id que o proprio
            # ML atribuiu. Descartar bloqueava o cadastro sem que houvesse
            # problema real.
            enumeracao_fechada = attr_type == "list"

            # Tenta casar value_id na lista — vale para os dois tipos: quando
            # casa, aproveitamos o id e o nome exato do ML.
            if value_name and allowed:
                casou = False
                for v in allowed:
                    if v["name"].lower() == value_name.lower():
                        value_id = v["id"]
                        value_name = v["name"]
                        casou = True
                        break

                # Nao casou: DESCARTA o valor em vez de gravar nome sem id.
                #
                # O ML recusa atributo de lista sem value_id — a resposta e
                # `Attribute [X] is not valid, item values [(null:Y)]`, obscura e
                # so na hora de publicar, depois de ja ter gasto geracao de
                # imagem e descricao. Gravar um valor que a categoria nao conhece
                # nao ajuda ninguem: melhor deixar vazio e cair no fluxo normal
                # de "atributo pendente", que pede o valor certo ao seller.
                #
                # Acontece de verdade quando o prefill vem de outra categoria:
                # "Colônia" e valido em MLB178938 (perfume pet) e inexistente em
                # MLB6284 (perfumes), onde o equivalente chama "Água de colônia".
                if not casou and enumeracao_fechada:
                    logger.warning(
                        "atributo_descartado attribute_id=%s valor=%r categoria=%s "
                        "motivo=fora_dos_allowed_values opcoes=%s",
                        attr_id,
                        value_name,
                        listing.ml_category_id,
                        [v.get("name") for v in allowed][:10],
                    )
                    value_name = None
                    value_id = None
                elif not casou:
                    # Sugestao, nao enumeracao: mantem o texto livre e deixa o
                    # ML resolver o id. Logado em info porque nao e problema —
                    # so o registro de que a marca/valor nao estava na lista.
                    logger.info(
                        "atributo_texto_livre attribute_id=%s valor=%r categoria=%s "
                        "motivo=fora_das_sugestoes_mas_tipo_nao_e_lista",
                        attr_id,
                        value_name,
                        listing.ml_category_id,
                    )

            if not value_name and is_required:
                has_unfilled_required = True

            self.db.add(ListingAttribute(
                listing_id=listing.id,
                attribute_id=attr_id,
                attribute_name=attr["name"],
                attribute_type=attr_type,
                is_required=is_required,
                value_id=value_id,
                value_name=value_name,
                source="seller" if attr_id in prefill and prefill.get(attr_id) else "ai",
                allowed_values=allowed,
            ))

        listing.status = "pending_seller_attributes" if has_unfilled_required else "pending_description"
