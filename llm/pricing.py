"""Cost of a model call. Prices come from the catalog (OpenRouter's own price per model, or the
price entered for a Bedrock model) and otherwise from the genai-prices package (Pydantic's price table). Unknown models give None, never 0."""
from genai_prices import Usage, calc_price

_PROVIDER_IDS = {"anthropic": "anthropic", "bedrock": "aws"}


def _bedrock_ref(name):
    for prefix in ("eu.", "us.", "global.", "apac.", "us-gov."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def cost_usd(provider, model_name, input_tokens, output_tokens, price_in=None, price_out=None):
    """USD for one call, or None when the price is unknown (a local model, an unlisted model)."""
    if price_in is not None and price_out is not None and (price_in < 0 or price_out < 0):
        return None  # OpenRouter lists "price varies" as -1 per token: never count that as negative spend
    if provider in ("openrouter", "bedrock") and price_in is not None and price_out is not None:
        return input_tokens * price_in + output_tokens * price_out  # the catalog price, so a new model is billed too
    provider_id = _PROVIDER_IDS.get(provider)
    if provider_id is None:
        return None
    ref = _bedrock_ref(model_name) if provider == "bedrock" else model_name
    try:
        return float(calc_price(Usage(input_tokens=input_tokens, output_tokens=output_tokens),
                                model_ref=ref, provider_id=provider_id).total_price)
    except LookupError:
        return None
