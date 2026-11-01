"""Estrazione di dati da una foto con un modello di visione (Claude).

La funzione `estrai_da_immagine` è generica: riceve un'immagine, un'istruzione e uno
schema JSON e restituisce i dati. Il vocabolario è solo un caso d'uso; per gli scontrini
basta scrivere un altro prompt e un altro schema (vedi in fondo).
"""

import base64
import json

import anthropic

MODELLO = "claude-opus-5-5"

_client = anthropic.Anthropic()  # legge ANTHROPIC_API_KEY dall'ambiente


class EstrazioneFallita(Exception):
    pass


def estrai_da_immagine(immagine: bytes, media_type: str, istruzione: str, schema: dict) -> dict:
    """Manda l'immagine al modello e restituisce il JSON validato secondo `schema`."""
    dati_b64 = base64.standard_b64encode(immagine).decode("utf-8")
    risposta = _client.beta.messages.create(
        model=MODELLO,
        max_tokens=16000,
        # Se il modello principale rifiuta la richiesta, l'API ripiega da sola su un altro modello.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": dati_b64}},
                {"type": "text", "text": istruzione},
            ],
        }],
    )
    if risposta.stop_reason == "refusal":
        raise EstrazioneFallita("Il modello ha rifiutato di elaborare l'immagine.")
    if risposta.stop_reason == "max_tokens":
        raise EstrazioneFallita("Risposta troppo lunga: prova con una foto di mezza pagina.")
    testo = next((b.text for b in risposta.content if b.type == "text"), None)
    if not testo:
        raise EstrazioneFallita("Il modello non ha restituito dati.")
    return json.loads(testo)


# ---------------------------------------------------------------------------
# Caso d'uso 1: vocabolario tedesco
# ---------------------------------------------------------------------------

SCHEMA_VOCABOLARIO = {
    "type": "object",
    "properties": {
        "trascrizione": {"type": "string"},
        "parole": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "forma_base": {"type": "string"},
                    "articolo": {"type": "string", "enum": ["der", "die", "das", ""]},
                    "plurale": {"type": "string"},
                    "categoria": {"type": "string", "enum": [
                        "sostantivo", "verbo", "aggettivo", "avverbio", "preposizione",
                        "congiunzione", "pronome", "articolo", "numerale", "espressione", "altro"]},
                    "traduzione": {"type": "string"},
                    "esempio": {"type": "string"},
                    "livello": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"]},
                    "molto_comune": {"type": "boolean"},
                },
                "required": ["forma_base", "articolo", "plurale", "categoria", "traduzione",
                             "esempio", "livello", "molto_comune"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["trascrizione", "parole"],
    "additionalProperties": False,
}

ISTRUZIONE_VOCABOLARIO = """La foto mostra una pagina scritta in tedesco (libro, quaderno o appunti).

1. Trascrivi fedelmente il testo tedesco nel campo "trascrizione".
2. Estrai le parole significative, una sola volta ciascuna. Per ogni parola:
   - forma_base: infinito per i verbi, nominativo singolare per i nomi (senza articolo), forma non declinata per gli aggettivi. Le espressioni fisse restano intere (es. "Wie geht's?").
   - articolo: der, die o das per i sostantivi; stringa vuota per tutto il resto.
   - plurale: il plurale dei sostantivi (es. "Hunde"); stringa vuota se non si usa o se non è un sostantivo.
   - categoria: il tipo di parola.
   - traduzione: traduzione italiana breve, più significati separati da " / ".
   - esempio: la frase della pagina in cui compare la parola (copiata così com'è); se la pagina non ha frasi, una frase breve e semplice.
   - livello: il livello QCER a cui di solito si impara la parola.
   - molto_comune: true per parole funzionali o elementari come und, der, die, das, ein, ist, ich, du, nicht, ja, in, mit, zu; false per le altre.
Non inventare parole che non sono nella foto. Ignora nomi propri, numeri di pagina e intestazioni del libro."""


# ---------------------------------------------------------------------------
# Caso d'uso 2 (esempio per il futuro): scontrino
# ---------------------------------------------------------------------------
# SCHEMA_SCONTRINO = {"type": "object", "properties": {"negozio": {"type": "string"},
#     "data": {"type": "string"}, "totale": {"type": "number"}, "valuta": {"type": "string"},
#     "righe": {"type": "array", "items": {...}}}, ...}
# ISTRUZIONE_SCONTRINO = "La foto mostra uno scontrino. Estrai negozio, data, totale e righe..."
# dati = estrai_da_immagine(foto, "image/jpeg", ISTRUZIONE_SCONTRINO, SCHEMA_SCONTRINO)
