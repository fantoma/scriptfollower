# Script Follower su GitHub: pacchetti portable per Linux, Windows e macOS

Funziona esattamente come CueStage. GitHub Actions costruisce e prova Script Follower su macchine Linux,
Windows e Mac messe a disposizione da GitHub. Tu carichi il progetto una volta. Poi, per ogni nuova
versione, basta `./release.sh`: dopo circa mezz'ora la pagina **Releases** contiene i pacchetti.

| File | Per chi |
|---|---|
| `ScriptFollower-0.2.1-x86_64.AppImage` | Linux (Ubuntu/Xubuntu 22.04+, Debian 12+, Fedora 35+…), un solo file |
| `ScriptFollower-0.2.1-windows-portable.zip` | Windows 10/11, senza installazione (anche da chiavetta) |
| `ScriptFollower-0.2.1-macos-arm64.dmg` | Mac Apple Silicon (M1, M2, M3, M4) |
| `ScriptFollower-0.2.1-macos-x86_64.dmg` | Mac Intel |

Tutti i pacchetti sono **portable** e contengono già il **modello vocale italiano** (`vosk-model-small-it-0.22`):
non serve installare nulla, né scaricare altro.

Prima di creare ogni pacchetto, GitHub prova Script Follower su quel sistema:
- test di regressione;
- `--self-test` del pacchetto finito: PortAudio, PDF, Vosk con il modello caricato davvero, plugin OSC,
  finestra.

Se qualcosa non va, la Release non riceve quel file e il motivo si legge nella scheda **Actions**.

---

## 1. Account e repository (una volta sola)

L'account GitHub ce l'hai già (lo stesso di CueStage, `fantoma`). Scegli se il repository sarà **pubblico** o
**privato**:

- **Pubblico** (come CueStage): GitHub Actions è **gratuito senza limiti** di minuti e di spazio.
- **Privato**: il piano Free comprende 2000 minuti al mese e 500 MB per gli artifact.
  - I minuti Windows contano doppio e quelli Mac 10 volte.
  - Una versione completa costa circa 300–400 minuti.
  - Il conto è **condiviso con CueStage** se anche quello è privato.

## 2. Caricare il progetto (da Ubuntu, una volta sola)

La cartella che ti ho mandato è già un repository git, con il primo commit fatto. Se `git` e `gh` sono già
configurati per CueStage, bastano due comandi:

```bash
unzip scriptfollower-0.2.1.zip && cd scriptfollower
./crea_repo_github.sh                 # crea fantoma/scriptfollower (pubblico) e carica tutto
```

Lo script chiede conferma prima di fare qualsiasi cosa. Accetta queste opzioni:
- `--privato` crea un repository privato;
- `--nome altro-nome` usa un altro nome.

Se preferisci farlo a mano:

```bash
gh auth status                        # deve dire «Logged in to github.com account fantoma»
gh repo create scriptfollower --public --source=. --push      # oppure --private
```

Fatto: il progetto è su `https://github.com/fantoma/scriptfollower`. Nella scheda **Actions** vedrai partire
subito il primo «Build». È il test su Linux e dura circa 3 minuti; il segno verde ✔ vuol dire che è riuscito.

Su un computer nuovo, prima dei comandi sopra:

```bash
sudo apt install git gh
gh auth login                         # GitHub.com › HTTPS › Login with a web browser
git config --global user.name  "Il tuo nome"
git config --global user.email "la-tua-email@esempio.it"
```

## 3. Pubblicare una versione (ogni volta)

```bash
./release.sh              # chiede il numero (propone il successivo, es. 0.2.2)
./release.sh 0.3.0        # oppure lo indichi
./release.sh --prova      # mostra cosa farebbe, senza cambiare né inviare nulla
```

Come in CueStage, lo script fa queste cose, nell'ordine:
1. controlla git, il collegamento a GitHub e che la versione sia nuova;
2. se vuoi, esegue i test di regressione in locale;
3. scrive la versione in `scriptfollower/__init__.py`, fa il commit e lo invia;
4. crea il tag `vX.Y.Z` e lo invia: GitHub Actions costruisce i pacchetti e li pubblica nella Release;
5. con `gh`, ti fa seguire la costruzione e alla fine mostra il link alla Release.

Su GitHub › **Actions** il lavoro «Build» del tag ha queste fasi:
- **Test** su Ubuntu, Windows e macOS;
- **Prepara la Release**, che controlla che il tag corrisponda alla versione;
- **Linux AppImage**, **Windows** e **macOS (arm64 / x86_64)**, in parallelo.

Se la costruzione fallisce, correggi e ripubblica la stessa versione:
```bash
git tag -d v0.2.2 && git push origin :refs/tags/v0.2.2
./release.sh 0.2.2
```

### Prova senza pubblicare

GitHub › **Actions** › **Build** › **Run workflow**.
- Costruisce tutti i pacchetti senza creare una Release.
- I file restano 3 giorni sotto **Artifacts**.

## 4. Quando qualcosa va storto

- **Actions** › il lavoro con la ✘ rossa › il passaggio con la ✘ › leggi le ultime righe del registro.
- Il **self-test** stampa una riga ✔ o ✘ per ogni parte: PortAudio, PDF, Vosk, modello vocale, plugin OSC,
  interfaccia.
- Se il download del modello da alphacephei.com non riesce (sito momentaneamente irraggiungibile), rilancia il
  lavoro: *Re-run failed jobs*.
- Se il lavoro macOS Intel dice che `macos-15-intel` non esiste:
  - in `.github/workflows/build.yml`, sostituiscilo con il nome indicato da GitHub;
  - oppure togli le due righe `- runner: macos-15-intel` / `arch: x86_64`.

## 5. Avvisi al primo avvio

- **Windows**: «Windows ha protetto il PC» › *Ulteriori informazioni* › *Esegui comunque*.
- **macOS**: tasto destro su Script Follower › *Apri* › *Apri* (basta la prima volta).
  - Al primo ASCOLTA, macOS chiede il permesso per il **microfono**: rispondi *Consenti*.
  - Alla domanda sulla **rete locale** rispondi *Consenti*: serve per inviare l'OSC.

La firma e la notarizzazione Apple si attivano con gli stessi segreti del repository di CueStage
(`MACOS_CERT_P12`, `MACOS_CERT_PASSWORD`, `MACOS_CODESIGN_ID`, `APPLE_ID`, `APPLE_TEAM_ID`,
`APPLE_APP_PASSWORD`). Vanno ricreati in questo repository: GitHub › **Settings** › **Secrets and
variables** › **Actions**. Vedi il punto 5 di GITHUB.md di CueStage.

## 6. Costruire a mano (senza GitHub)

| Sistema | Comando (dalla cartella del progetto) | Requisiti |
|---|---|---|
| Linux | `packaging/build_appimage.sh` | — |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` | Python 3.12 da python.org |
| macOS | `packaging/build_macos.sh` | Python 3.12 da python.org e `xcode-select --install` |

`MODEL_URL=""` costruisce senza modello. I file finiscono nella cartella `dist/`.

Per installare dai sorgenti su Ubuntu (senza AppImage): `./install.sh`.
