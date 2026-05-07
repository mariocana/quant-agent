"""Client Anthropic condiviso con timeout e retry robusti.

Usa questo helper invece di istanziare Anthropic() direttamente,
così tutti gli agenti hanno gestione errori coerente.
"""
import time
from anthropic import Anthropic, APITimeoutError, APIConnectionError, RateLimitError
from loguru import logger


def make_client(api_key: str, timeout_seconds: float = 180.0) -> Anthropic:
    """Crea un client Anthropic con timeout esteso (default 3 min).
    
    La generazione MQL5 può richiedere 60-90 secondi per output lunghi.
    Il default httpx di 60s è troppo basso.
    """
    return Anthropic(
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=2,  # retry automatico su errori transient
    )


def call_with_retry(
    client: Anthropic,
    model: str,
    system: str,
    messages: list,
    max_tokens: int = 4096,
    max_attempts: int = 3,
    initial_backoff: float = 5.0,
):
    """Chiama l'API con retry esponenziale su errori di rete/timeout.
    
    Returns: il content text, oppure raise dopo max_attempts fallimenti.
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except (APITimeoutError, APIConnectionError) as e:
            last_error = e
            backoff = initial_backoff * (2 ** (attempt - 1))
            logger.warning(
                f"⚠️  API connection issue (attempt {attempt}/{max_attempts}): "
                f"{type(e).__name__}. Retry in {backoff}s..."
            )
            if attempt < max_attempts:
                time.sleep(backoff)
        except RateLimitError as e:
            last_error = e
            backoff = 30.0 * attempt  # rate limit: aspetta di più
            logger.warning(f"⚠️  Rate limit hit. Sleep {backoff}s...")
            if attempt < max_attempts:
                time.sleep(backoff)
        except Exception as e:
            # Errori non-retriable (auth, bad request, etc.)
            logger.error(f"❌ API call failed (non-retriable): {e}")
            raise
    
    raise last_error
