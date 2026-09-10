import re


# ── Variable resolution ────────────────────────────────────────────────────────

_TOKEN_MAP = {
    "{descricao_erp}":       lambda d: d.get("sku_description") or "",
    "{marca}":                lambda d: d.get("sku_brand") or "",
    "{modelo}":               lambda d: d.get("sku_model") or "",
    "{referencia_tecnica}":   lambda d: d.get("technical_reference") or "",
    "{aplicacao_veiculo}":    lambda d: d.get("vehicle_application") or "",
    "{cor}":                  lambda d: d.get("color") or "",
    "{tamanho}":              lambda d: d.get("size") or "",
    "{capacidade}":           lambda d: d.get("capacity") or "",
    "{material}":             lambda d: d.get("material") or "",
    "{genero}":               lambda d: d.get("gender") or "",
    "{condicao}":             lambda d: "Novo" if d.get("condition") == "new" else "Usado",
    "{ean}":                  lambda d: d.get("ean") or "",
    "{seo}":                  lambda d: d.get("seo_context") or "",
}

_DEFAULT_STRUCTURE = "{descricao_erp} {marca}"


def _resolve_structure(structure: str, data: dict) -> str:
    result = structure
    for token, fn in _TOKEN_MAP.items():
        result = result.replace(token, fn(data))
    # Collapse multiple spaces left by empty substitutions
    result = re.sub(r"\s+", " ", result).strip()
    return result


# ── Title prompt ───────────────────────────────────────────────────────────────

def build_title_prompt(
    sku_description: str,
    sku_brand: str,
    condition: str,
    ean: str | None = None,
    seo_context: str | None = None,
    batch_mode: bool = False,
    title_config: dict | None = None,
    sku_model: str | None = None,
    technical_reference: str | None = None,
    vehicle_application: str | None = None,
    color: str | None = None,
    size: str | None = None,
    capacity: str | None = None,
    material: str | None = None,
    gender: str | None = None,
) -> str:
    condition_pt = "Novo" if condition == "new" else "Usado"

    product_data = {
        "sku_description": sku_description,
        "sku_brand": sku_brand,
        "sku_model": sku_model or "",
        "technical_reference": technical_reference or "",
        "vehicle_application": vehicle_application or "",
        "color": color or "",
        "size": size or "",
        "capacity": capacity or "",
        "material": material or "",
        "gender": gender or "",
        "condition": condition,
        "ean": ean or "",
        "seo_context": seo_context or "",
    }

    structure = title_config["structure"] if title_config else _DEFAULT_STRUCTURE
    extra_rules = title_config.get("rules") or "" if title_config else ""
    resolved_example = _resolve_structure(structure, product_data)

    ean_line = f"EAN/GTIN: {ean}" if ean else ""
    seo_line = f"Contexto SEO adicional: {seo_context}" if seo_context else ""
    extra_rules_line = f"\nREGRAS ESPECÍFICAS DESTE SELLER:\n{extra_rules}" if extra_rules else ""

    if batch_mode:
        return f"""Você é um especialista em SEO para o Mercado Livre Brasil.
Gere EXATAMENTE 1 título otimizado para o produto abaixo.

REGRAS OBRIGATÓRIAS:
- Idioma: PORTUGUÊS DO BRASIL (nunca inglês, nunca espanhol)
- Máximo 60 caracteres (incluindo espaços)
- Estrutura preferencial: {structure}
- Exemplo de aplicação da estrutura: {resolved_example}
- Coloque os termos mais específicos e buscados nos primeiros 30 caracteres
- Não use: pontuação desnecessária, maiúsculas em excesso, artigos (o, a, os, as)
- Palavras PROIBIDAS pelo ML: Melhor, Promoção, Oferta, Barato, Grátis, Desconto
- Máximo de informação útil no mínimo de palavras
- PRESERVE no título TODA palavra de tipo de produto (categoria comercial) que \
estiver na Descrição do ERP — ex.: Perfume, Colônia, Body Splash, Desodorante, \
Creme, Sabonete. Essas palavras decidem a categoria do anúncio: NUNCA omita uma \
delas por economia de caracteres (corte outra coisa antes). Não invente tipo de \
produto que não esteja na origem.
{extra_rules_line}
PRODUTO:
Descrição do ERP: {sku_description}
Marca: {sku_brand}
Condição: {condition_pt}
{ean_line}
{seo_line}

Responda EXCLUSIVAMENTE em JSON válido, sem texto antes ou depois, sem markdown:
{{"title": "título em português aqui"}}"""

    return f"""Você é um especialista em SEO para o Mercado Livre Brasil.
Crie 3 variações de título para um anúncio do produto abaixo.

REGRAS OBRIGATÓRIAS:
- Máximo 60 caracteres por título (incluindo espaços)
- Estrutura preferencial: {structure}
- Exemplo de aplicação da estrutura: {resolved_example}
- Coloque os termos mais específicos e buscados nos primeiros 30 caracteres
- Não use: pontuação desnecessária, maiúsculas em excesso, artigos (o, a, os, as)
- Palavras PROIBIDAS pelo ML: Melhor, Promoção, Oferta, Barato, Grátis, Desconto
- Priorize termos que compradores realmente buscam no Brasil
{extra_rules_line}
PRODUTO:
Descrição do ERP: {sku_description}
Marca: {sku_brand}
Condição: {condition_pt}
{ean_line}
{seo_line}

Responda EXCLUSIVAMENTE em JSON válido, sem markdown, sem ```json:
{{"titles": [{{"title": "título aqui", "score": 9.2, "rationale": "motivo breve"}}, {{"title": "título aqui", "score": 8.7, "rationale": "motivo breve"}}, {{"title": "título aqui", "score": 8.1, "rationale": "motivo breve"}}]}}"""


# ── Image prompt ───────────────────────────────────────────────────────────────

def build_image_prompt_request(brand: str, title: str, description: str) -> str:
    return f"""You are an expert prompt engineer for Google Imagen 4 AI image generation.

Write a concise English prompt (max 80 words) to generate a professional e-commerce product photo suitable for Mercado Livre listings.

MANDATORY rules for the prompt you write:
- Describe ONLY the physical product: exact shape, material, color, size, key visual features
- The product must be ISOLATED on a pure white (#FFFFFF) background with neutral studio lighting
- The product must occupy at least 80% of the frame, centered
- Image must be square (1:1), minimum 1024×1024 pixels equivalent
- Start with the product type in English (e.g. "ball bearing", "spiral notebook", "power drill")
- NO people, hands, clothing, text, watermarks, logos, backgrounds, shadows, props or scene elements

WHAT MUST NEVER APPEAR (these cause listing rejection on Mercado Livre):
- Any element that is NOT the product itself
- Lifestyle scenes, nature, furniture, rooms
- Graphic overlays, price tags, promotional banners
- Multiple products unless it is a kit/set

Product:
Brand: {brand}
Title: {title}
Description: {description}

Output ONLY the Imagen prompt. No explanations, no markdown, no quotes."""


# ── Description prompt ─────────────────────────────────────────────────────────

def build_description_prompt(listing_data: dict) -> str:
    attrs_text = "\n".join(
        f"- {a['attribute_name']}: {a['value_name']}"
        for a in listing_data.get("attributes", [])
        if a.get("value_name")
    )
    condition_pt = "Novo" if listing_data.get("condition") == "new" else "Usado"

    return f"""Você é um redator especialista em e-commerce para o Mercado Livre Brasil.
Crie uma descrição de produto atrativa e informativa para o anúncio abaixo.

REGRAS:
- Use HTML simples: <h2>, <p>, <ul>, <li>, <strong>
- Não use: scripts, CSS inline, iframes, tabelas complexas
- Estrutura: parágrafo introdutório > benefícios principais > especificações > informações adicionais
- Tom: profissional, direto, focado em benefícios para o comprador
- Idioma: português brasileiro
- Extensão: entre 200 e 400 palavras no texto (sem contar as tags HTML)

DADOS DO PRODUTO:
Título do anúncio: {listing_data.get("selected_title") or listing_data.get("title")}
Marca: {listing_data.get("sku_brand") or listing_data.get("brand")}
Condição: {condition_pt}
Descrição original: {listing_data.get("sku_description") or listing_data.get("description")}

ATRIBUTOS CONFIRMADOS:
{attrs_text or "Não informados"}

Responda EXCLUSIVAMENTE com o HTML, sem markdown, sem ```html, sem explicações."""


# ── Card copy prompt ─────────────────────────────────────────────────────────

def build_card_copy_prompt(source: dict) -> str:
    """Copy dos 3 cards de imagem (benefícios, uso, especificações).

    Roda ANTES de `generate_description` no pipeline, então não há descrição
    gerada disponível ainda — a matéria-prima é só título, descrição do ERP,
    marca, modelo e os atributos já confirmados. Por isso a regra
    anti-invenção é a mais importante do prompt: com pouco material de
    origem, é fácil o modelo "completar" uma especificação que não existe.
    """
    attrs_text = "\n".join(
        f"- {a['attribute_name']}: {a['value_name']}"
        for a in source.get("attributes", [])
        if a.get("value_name")
    )

    return f"""Você é um redator especialista em e-commerce para o Mercado Livre Brasil.
Crie a copy de 3 cards de imagem para o anúncio abaixo: benefícios, modo de uso e especificações.

REGRAS OBRIGATÓRIAS:
- Idioma: PORTUGUÊS DO BRASIL
- Cada card tem um "title" (até 40 caracteres) e uma lista "bullets" com 2 a 3 itens (até 50 caracteres cada)
- NUNCA invente especificação técnica (medida, composição, voltagem, capacidade, material) que não esteja no texto de origem abaixo
- Se não houver dado suficiente para algum card (ex.: produto sem instrução de uso clara), escreva algo GENÉRICO e SEGURO em vez de inventar detalhe
- Não mencione preço, prazo de entrega, concorrentes, nem use superlativo não comprovável ("o melhor do mercado", "número 1")

DADOS DE ORIGEM:
Título do anúncio: {source.get("selected_title") or ""}
Descrição do ERP: {source.get("sku_description") or ""}
Marca: {source.get("sku_brand") or ""}
Modelo: {source.get("sku_model") or ""}

ATRIBUTOS CONFIRMADOS:
{attrs_text or "Não informados"}

Responda EXCLUSIVAMENTE em JSON válido, sem markdown, sem ```json:
{{"benefits": {{"title": "título até 40 chars", "bullets": ["bullet até 50 chars", "bullet até 50 chars"]}}, "usage": {{"title": "título até 40 chars", "bullets": ["bullet até 50 chars", "bullet até 50 chars"]}}, "specs": {{"title": "título até 40 chars", "bullets": ["bullet até 50 chars", "bullet até 50 chars"]}}}}"""
