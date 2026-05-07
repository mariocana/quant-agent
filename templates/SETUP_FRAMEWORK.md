# Setup del Framework MQL5

## Installazione una tantum (da fare la prima volta)

Il file `PropAgentFramework.mq5` deve essere compilato in MT5 una sola volta.
Da quel momento il sistema lo userà sempre senza bisogno di rigenerare codice.

### Passi:

1. **Apri MT5** sulla VM/PC

2. **Apri MetaEditor**: tasto F4 dentro MT5, oppure menu Tools → MetaQuotes Language Editor

3. **Copia il file `PropAgentFramework.mq5`** dalla cartella `templates/` del progetto in:
   ```
   <DataFolder>\MQL5\Experts\PropAgent\PropAgentFramework.mq5
   ```
   
   Per trovare il DataFolder di MT5: in MT5 menu File → Open Data Folder

4. **Compila**: in MetaEditor apri il file e premi F7. Dovresti vedere:
   ```
   0 errors, 0 warnings — compilation successful
   ```
   Se ci sono errori, segnala — il framework va corretto.

5. **Verifica nel Navigator di MT5**:
   - Apri MT5 → View → Navigator (Ctrl+N)
   - Sotto "Expert Advisors" → cartella PropAgent → vedrai `PropAgentFramework`

## Come funziona dopo l'installazione

- **Mode SPEC** (90% dei casi): il sistema genera solo un file `.set` con i parametri,
  poi lancia il framework con quel `.set`. Zero compile, zero errori sintassi.
  
- **Mode CUSTOM** (idee complesse): il sistema genera codice `.mq5` da zero come prima,
  con auto-fix se il compile fallisce.

## Aggiungere nuove strategie al framework

Quando vuoi aggiungere un nuovo tipo di strategia (es: "ichimoku_cloud"):

1. Aggiungi entry in `ENUM_STRATEGY_TYPE` in `PropAgentFramework.mq5`
2. Aggiungi parametri input nel gruppo apposito
3. Implementa funzione `SignalIchimokuCloud()` che ritorna 0/1/-1
4. Aggiungi case nel switch in `OnTick()`
5. Ricompila il framework
6. Aggiungi entry in `SUPPORTED_STRATEGY_TYPES` di `agents/spec_generator.py`
7. Aggiorna il `SYSTEM_PROMPT` per documentare la nuova strategia

In ~30 minuti hai un nuovo tipo aggiunto.
