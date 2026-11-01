# Bot Telegram "Vocabolario Tedesco"

Fai la foto di una pagina in tedesco e la mandi al bot. Il bot legge la pagina con un modello di visione (Claude), estrae le parole significative (forma base, articolo, plurale, traduzione italiana, frase d'esempio, livello QCER), scarta i doppioni e le salva in un file SQLite. Poi ti risponde con il riepilogo e un pulsante ❌ per togliere ogni parola che non vuoi.

## Cosa serve

- Python 3.10 o più recente
- Il token del bot (da BotFather, vedi sotto)
- Una chiave API di Anthropic: https://console.anthropic.com/settings/keys (ogni foto costa pochi centesimi)

## 1. Creare il bot

1. In Telegram cerca **BotFather** (quello con la spunta blu) e avvialo.
2. Manda `/newbot`.
3. Scegli il nome visibile (es. `Vocabolario Tedesco`).
4. Scegli lo username, che deve finire con `bot` (es. `karim_tedesco_bot`).
5. Copia il token che ti restituisce. Non condividerlo e non metterlo su GitHub.

## 2. Preparare il computer

Apri il terminale nella cartella `bot-telegram`:

```
python3 -m venv .venv
source .venv/bin/activate        # su Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # su Windows: copy .env.example .env
```

Apri `.env` e incolla il token di Telegram e la chiave di Anthropic.

## 3. Avviare

```
python bot.py
```

Finché la finestra resta aperta il bot risponde. Ctrl+C per fermarlo.

Al primo `/start` il bot ti dice il tuo id Telegram. Mettilo in `UTENTI_AUTORIZZATI` nel file `.env` (più id separati da virgola) e riavvia: così nessun altro può usare il bot e consumare la tua chiave.

## Comandi

- mandare una foto: aggiunge le parole (meglio ancora se la mandi "come file", non compressa)
- `/parole`: le ultime 20 parole
- `/esporta`: tutto il vocabolario in un file CSV (si apre con Excel)

## Filtro sulle parole

Nel file `.env`, `FILTRO` decide cosa entra:

- `comuni` (predefinito): salta le parole elementari come und, der, ist, ich
- `nessuno`: tiene tutto
- `A2`, `B1`, `B2`, `C1`: tiene solo le parole da quel livello in su

Per i libri di Gabri (A1) va bene `comuni`; con `B1` si perderebbe quasi tutto.

## Come è fatto

- `bot.py`: Telegram (polling, nessun server), riceve la foto, risponde e gestisce i pulsanti
- `estrazione.py`: manda la foto al modello. La funzione `estrai_da_immagine` è generica: per gli scontrini basta un altro prompt e un altro schema JSON (c'è un esempio in fondo al file)
- `db.py`: database SQLite e controllo dei duplicati (confronta articolo + forma base, senza distinguere maiuscole e ß/ss)
- `vocabolario.db`: viene creato al primo avvio, è il tuo vocabolario
