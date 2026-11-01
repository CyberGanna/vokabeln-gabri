/**
 * Bot Telegram "Vokabeln di Gabri" su Google Apps Script.
 *
 * Gira nel cloud di Google, gratis, anche a Mac spento.
 * Ogni minuto un attivatore controlla se su Telegram sono arrivate foto nuove,
 * le fa leggere a Claude, salva le parole nel foglio "Parole" di questo file
 * e le manda all'app di Gabri.
 *
 * Impostazioni (Impostazioni progetto > Proprietà script):
 *   TELEGRAM_TOKEN      token di BotFather
 *   ANTHROPIC_API_KEY   chiave sk-ant-...
 *   UTENTI_AUTORIZZATI  id Telegram separati da virgola
 *   FAMIGLIA            codice famiglia dell'app (es. family_varini)
 *   APP_URL             URL /exec dello script della dashboard dell'app
 *   FILTRO              comuni (predefinito) | nessuno | A2 | B1 | B2 | C1
 *
 * Primo avvio: scegli la funzione "installa" in alto e premi Esegui.
 */

const MODELLO = "claude-opus-5-5";
const FOGLIO = "Parole";
const COLONNE = ["id", "chiave", "articolo", "forma_base", "plurale", "categoria", "traduzione", "esempio", "livello", "utente", "data"];
const LIVELLI = ["A1", "A2", "B1", "B2", "C1", "C2"];
const TIPI_APP = { sostantivo: "s", aggettivo: "a", verbo: "v", avverbio: "av" };
const FORMATI_OK = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const MAX_BYTE = 4500000;

// ---------------------------------------------------------------------------
// Installazione e controllo
// ---------------------------------------------------------------------------

/** Da eseguire una volta: crea il foglio e l'attivatore che gira ogni minuto. */
function installa() {
  const p = impostazioni_();
  ["TELEGRAM_TOKEN", "ANTHROPIC_API_KEY", "UTENTI_AUTORIZZATI"].forEach(function (k) {
    if (!p[k]) throw new Error("Manca la proprietà " + k + " in Impostazioni progetto > Proprietà script.");
  });
  foglio_();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "controllaTelegram") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("controllaTelegram").timeBased().everyMinutes(1).create();
  telegram_("deleteWebhook", {});
  const me = telegram_("getMe", {});
  Logger.log("Fatto. Il bot @" + me.username + " ora gira su Google, ogni minuto.");
}

/** Ferma il bot su Google (per esempio per tornare alla versione sul Mac). */
function disinstalla() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  Logger.log("Attivatore tolto: il bot su Google è fermo.");
}

/** Gira ogni minuto: legge i messaggi nuovi da Telegram e li gestisce. */
function controllaTelegram() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return; // il giro precedente sta ancora lavorando
  try {
    const props = PropertiesService.getScriptProperties();
    const offset = Number(props.getProperty("OFFSET") || 0);
    const updates = telegram_("getUpdates", { offset: offset, timeout: 0, allowed_updates: ["message", "callback_query"] });
    if (!updates.length) return;
    // Segna subito come letti: se qualcosa va storto non rielabora (e non ripaga) la stessa foto.
    props.setProperty("OFFSET", String(updates[updates.length - 1].update_id + 1));
    updates.forEach(function (u) {
      try {
        if (u.callback_query) scarta_(u.callback_query);
        else if (u.message) gestisciMessaggio_(u.message);
      } catch (e) {
        console.error(e);
        const chat = (u.message && u.message.chat.id) || (u.callback_query && u.callback_query.message.chat.id);
        if (chat) invia_(chat, "Errore: " + String(e).slice(0, 300));
      }
    });
  } finally {
    lock.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Messaggi
// ---------------------------------------------------------------------------

function gestisciMessaggio_(msg) {
  const p = impostazioni_();
  const uid = msg.from && msg.from.id;
  const testo = (msg.text || "").trim();
  if (!autorizzato_(uid)) {
    if (testo.indexOf("/start") === 0) invia_(msg.chat.id, "Questo bot è privato. Il tuo id Telegram è " + uid + ".");
    console.warn("Messaggio ignorato da id " + uid);
    return;
  }
  if (msg.photo || msg.document) return riceviFoto_(msg);
  if (testo.indexOf("/start") === 0 || testo.indexOf("/help") === 0) {
    return invia_(msg.chat.id,
      "Ciao! Mandami la foto di una pagina in tedesco e aggiungo le parole al vocabolario.\n\n" +
      "Comandi:\n/parole – ultime 20 parole\n/esporta – link al foglio con tutto il vocabolario\n\n" +
      "Parole salvate: " + Math.max(foglio_().getLastRow() - 1, 0) + ". Filtro attivo: " + (p.FILTRO || "comuni") + ".\n" +
      "Il tuo id Telegram è " + uid + ". (Il bot ora gira su Google: risponde entro un minuto.)");
  }
  if (testo.indexOf("/parole") === 0) {
    const sh = foglio_(), n = sh.getLastRow() - 1;
    if (n < 1) return invia_(msg.chat.id, "Il vocabolario è ancora vuoto.");
    const righe = sh.getRange(Math.max(2, sh.getLastRow() - 19), 1, Math.min(20, n), COLONNE.length).getValues().reverse();
    return invia_(msg.chat.id, righe.map(function (r) { return "• " + etichetta_(r[2], r[3]) + " – " + r[6]; }).join("\n"));
  }
  if (testo.indexOf("/esporta") === 0) {
    return invia_(msg.chat.id, "Tutto il vocabolario è qui: " + SpreadsheetApp.getActiveSpreadsheet().getUrl());
  }
  invia_(msg.chat.id, "Mandami la foto di una pagina in tedesco. Comandi: /parole, /esporta.");
}

function riceviFoto_(msg) {
  const chat = msg.chat.id;
  let fileId, mime;
  if (msg.photo) {
    // Telegram manda la stessa foto in più risoluzioni: l'ultima è la più grande.
    fileId = msg.photo[msg.photo.length - 1].file_id;
    mime = "image/jpeg";
  } else {
    fileId = msg.document.file_id;
    mime = msg.document.mime_type || "";
    const nome = (msg.document.file_name || "").toLowerCase();
    if (FORMATI_OK.indexOf(mime) < 0) mime = /\.jpe?g$/.test(nome) ? "image/jpeg" : /\.png$/.test(nome) ? "image/png" : mime;
    if (FORMATI_OK.indexOf(mime) < 0) {
      return invia_(chat, "Questo formato non lo so leggere (" + (mime || "sconosciuto") + "). Mandami la foto come foto normale, non come file.");
    }
  }
  const attesa = invia_(chat, "Sto leggendo la pagina…");
  telegram_("sendChatAction", { chat_id: chat, action: "typing" });

  const file = telegram_("getFile", { file_id: fileId });
  const immagine = UrlFetchApp.fetch("https://api.telegram.org/file/bot" + impostazioni_().TELEGRAM_TOKEN + "/" + file.file_path).getBlob();
  if (immagine.getBytes().length > MAX_BYTE) {
    return modifica_(chat, attesa.message_id, "La foto è troppo grande. Mandala come foto normale (non come file).");
  }

  let dati;
  try {
    dati = estrai_(Utilities.base64Encode(immagine.getBytes()), mime);
  } catch (e) {
    console.error(e);
    return modifica_(chat, attesa.message_id, "Non sono riuscito a leggere la pagina: " + String(e.message || e).slice(0, 300));
  }

  const filtrate = filtra_(dati.parole);
  const esito = salva_(filtrate.tenute, msg.from.id);
  const nuove = esito.nuove;

  const righe = ["Ho aggiunto " + nuove.length + " parole nuove."];
  if (esito.doppie) righe.push(esito.doppie + " c'erano già.");
  if (filtrate.scartate) righe.push(filtrate.scartate + " scartate dal filtro (" + (impostazioni_().FILTRO || "comuni") + ").");
  if (nuove.length) {
    righe.push("");
    nuove.forEach(function (w) { righe.push("• " + etichetta_(w.articolo, w.forma_base) + " – " + w.traduzione); });
    righe.push("\nTocca una parola per toglierla.");
  }
  const app = inviaAllApp_(nuove);
  if (app) righe.push(app);

  const tastiera = nuove.slice(0, 40).map(function (w) {
    return [{ text: "❌ " + etichetta_(w.articolo, w.forma_base), callback_data: "del:" + w.id }];
  });
  modifica_(chat, attesa.message_id, righe.join("\n").slice(0, 4000), tastiera.length ? { inline_keyboard: tastiera } : null);
}

function scarta_(q) {
  if (!autorizzato_(q.from.id)) return telegram_("answerCallbackQuery", { callback_query_id: q.id });
  const id = String(q.data || "").replace("del:", "");
  const sh = foglio_();
  const ids = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues() : [];
  let tolta = null;
  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]) === id) {
      const r = sh.getRange(i + 2, 1, 1, COLONNE.length).getValues()[0];
      tolta = etichetta_(r[2], r[3]);
      sh.deleteRow(i + 2);
      break;
    }
  }
  telegram_("answerCallbackQuery", { callback_query_id: q.id, text: tolta ? "Tolta: " + tolta : "Già tolta" });
  const tastiera = ((q.message.reply_markup || {}).inline_keyboard || []).filter(function (r) { return r[0].callback_data !== q.data; });
  telegram_("editMessageReplyMarkup", {
    chat_id: q.message.chat.id, message_id: q.message.message_id,
    reply_markup: { inline_keyboard: tastiera },
  });
}

// ---------------------------------------------------------------------------
// Estrazione con Claude
// ---------------------------------------------------------------------------

const SCHEMA = {
  type: "object",
  properties: {
    parole: {
      type: "array",
      items: {
        type: "object",
        properties: {
          forma_base: { type: "string" },
          articolo: { type: "string", enum: ["der", "die", "das", ""] },
          plurale: { type: "string" },
          categoria: { type: "string", enum: ["sostantivo", "verbo", "aggettivo", "avverbio", "preposizione",
            "congiunzione", "pronome", "articolo", "numerale", "espressione", "altro"] },
          traduzione: { type: "string" },
          esempio: { type: "string" },
          livello: { type: "string", enum: LIVELLI },
          molto_comune: { type: "boolean" },
        },
        required: ["forma_base", "articolo", "plurale", "categoria", "traduzione", "esempio", "livello", "molto_comune"],
        additionalProperties: false,
      },
    },
  },
  required: ["parole"],
  additionalProperties: false,
};

const ISTRUZIONE = [
  "La foto mostra una pagina scritta in tedesco (libro, quaderno o appunti).",
  "",
  "Leggi con attenzione il testo tedesco ed estrai le parole significative, una sola volta ciascuna. Per ogni parola:",
  "   - forma_base: infinito per i verbi, nominativo singolare per i nomi (senza articolo), forma non declinata per gli aggettivi. Le espressioni fisse restano intere (es. \"Wie geht's?\").",
  "   - articolo: der, die o das per i sostantivi; stringa vuota per tutto il resto.",
  '   - plurale: il plurale dei sostantivi (es. "Hunde"); stringa vuota se non si usa o se non è un sostantivo.',
  "   - categoria: il tipo di parola.",
  '   - traduzione: traduzione italiana breve, più significati separati da " / ".',
  "   - esempio: la frase della pagina in cui compare la parola (copiata così com'è); se la pagina non ha frasi, una frase breve e semplice.",
  "   - livello: il livello QCER a cui di solito si impara la parola.",
  "   - molto_comune: true per parole funzionali o elementari come und, der, die, das, ein, ist, ich, du, nicht, ja, in, mit, zu; false per le altre.",
  "Non inventare parole che non sono nella foto. Ignora nomi propri, numeri di pagina e intestazioni del libro.",
].join("\n");

function estrai_(base64, mime) {
  const corpo = {
    model: MODELLO,
    max_tokens: 16000,
    fallbacks: "default", // se il modello rifiuta, l'API ripiega da sola su un altro modello
    output_config: { effort: "medium", format: { type: "json_schema", schema: SCHEMA } },
    messages: [{
      role: "user",
      content: [
        { type: "image", source: { type: "base64", media_type: mime, data: base64 } },
        { type: "text", text: ISTRUZIONE },
      ],
    }],
  };
  const r = UrlFetchApp.fetch("https://api.anthropic.com/v1/messages", {
    method: "post",
    contentType: "application/json",
    headers: {
      "x-api-key": impostazioni_().ANTHROPIC_API_KEY,
      "anthropic-version": "2026-10-21",
      "anthropic-beta": "server-side-fallback-2026-07-01",
    },
    payload: JSON.stringify(corpo),
    muteHttpExceptions: true,
  });
  const risposta = JSON.parse(r.getContentText());
  if (r.getResponseCode() !== 200) throw new Error("API " + r.getResponseCode() + ": " + ((risposta.error || {}).message || r.getContentText()).slice(0, 200));
  if (risposta.stop_reason === "refusal") throw new Error("il modello ha rifiutato di elaborare l'immagine");
  if (risposta.stop_reason === "max_tokens") throw new Error("risposta troppo lunga: prova con mezza pagina");
  const blocco = (risposta.content || []).filter(function (b) { return b.type === "text"; })[0];
  if (!blocco) throw new Error("il modello non ha restituito dati");
  return JSON.parse(blocco.text);
}

function filtra_(parole) {
  const f = String(impostazioni_().FILTRO || "comuni").trim().toUpperCase();
  let tenute;
  if (f === "NESSUNO") tenute = parole;
  else if (LIVELLI.indexOf(f) >= 0) tenute = parole.filter(function (w) { return LIVELLI.indexOf(w.livello) >= LIVELLI.indexOf(f); });
  else tenute = parole.filter(function (w) { return !w.molto_comune && w.categoria !== "articolo"; });
  return { tenute: tenute, scartate: parole.length - tenute.length };
}

// ---------------------------------------------------------------------------
// Foglio "Parole" (il vocabolario) e anti-duplicati
// ---------------------------------------------------------------------------

function foglio_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(FOGLIO);
  if (!sh) {
    sh = ss.insertSheet(FOGLIO);
    sh.getRange("A:K").setNumberFormat("@");
    sh.appendRow(COLONNE);
    sh.setFrozenRows(1);
  }
  return sh;
}

function chiave_(forma, articolo) {
  return ((articolo || "") + " " + (forma || "")).trim().toLowerCase().replace(/ß/g, "ss").replace(/\s+/g, " ").normalize("NFC");
}

function salva_(parole, utente) {
  const sh = foglio_();
  const esistenti = {};
  if (sh.getLastRow() > 1) sh.getRange(2, 2, sh.getLastRow() - 1, 1).getValues().forEach(function (r) { esistenti[r[0]] = true; });
  const oggi = Utilities.formatDate(new Date(), "Europe/Zurich", "yyyy-MM-dd HH:mm");
  const nuove = [], righe = [];
  let doppie = 0;
  parole.forEach(function (w) {
    const k = chiave_(w.forma_base, w.articolo);
    if (esistenti[k]) { doppie++; return; }
    esistenti[k] = true;
    w.id = Utilities.getUuid();
    nuove.push(w);
    righe.push([w.id, k, w.articolo, w.forma_base, w.plurale, w.categoria, w.traduzione, w.esempio, w.livello, String(utente), oggi]);
  });
  if (righe.length) sh.getRange(sh.getLastRow() + 1, 1, righe.length, COLONNE.length).setValues(righe);
  return { nuove: nuove, doppie: doppie };
}

// ---------------------------------------------------------------------------
// Collegamento con l'app di Gabri
// ---------------------------------------------------------------------------

function inviaAllApp_(nuove) {
  const p = impostazioni_();
  if (!p.APP_URL || !p.FAMIGLIA || !nuove.length) return "";
  const corpo = {
    app: "vokabeln-gabri", tipo: "parole", famiglia: p.FAMIGLIA,
    parole: nuove.map(function (w) { return { de: etichetta_(w.articolo, w.forma_base), it: w.traduzione, tipo: TIPI_APP[w.categoria] || "x" }; }),
  };
  try {
    const r = UrlFetchApp.fetch(p.APP_URL, {
      method: "post", contentType: "text/plain;charset=utf-8",
      payload: JSON.stringify(corpo), followRedirects: true, muteHttpExceptions: true,
    });
    const t = r.getContentText().trim();
    if (r.getResponseCode() === 200 && t.toLowerCase().indexOf("ok") === 0) return "Inviate all'app di Gabri ✅";
    return "⚠️ Il Foglio Google ha risposto: " + t.slice(0, 100) + " (le parole restano salvate qui).";
  } catch (e) {
    console.error(e);
    return "⚠️ Non sono riuscito a mandarle all'app (restano salvate qui).";
  }
}

// ---------------------------------------------------------------------------
// Utilità
// ---------------------------------------------------------------------------

function impostazioni_() {
  return PropertiesService.getScriptProperties().getProperties();
}

function autorizzato_(uid) {
  const lista = String(impostazioni_().UTENTI_AUTORIZZATI || "").replace(/\s/g, "").split(",").filter(String);
  return lista.length === 0 || lista.indexOf(String(uid)) >= 0;
}

function etichetta_(articolo, forma) {
  return (articolo ? articolo + " " : "") + forma;
}

function telegram_(metodo, dati) {
  const r = UrlFetchApp.fetch("https://api.telegram.org/bot" + impostazioni_().TELEGRAM_TOKEN + "/" + metodo, {
    method: "post", contentType: "application/json", payload: JSON.stringify(dati), muteHttpExceptions: true,
  });
  const j = JSON.parse(r.getContentText());
  if (!j.ok) throw new Error("Telegram " + metodo + ": " + j.description);
  return j.result;
}

function invia_(chat, testo) {
  return telegram_("sendMessage", { chat_id: chat, text: testo });
}

function modifica_(chat, messageId, testo, tastiera) {
  const dati = { chat_id: chat, message_id: messageId, text: testo };
  if (tastiera) dati.reply_markup = tastiera;
  return telegram_("editMessageText", dati);
}
