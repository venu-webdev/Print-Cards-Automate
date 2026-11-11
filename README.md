# Card Print Studio

The shop's tool for turning document PDFs into print-ready card images (1016 × 638 px, 300 DPI,
85.6 × 54 mm) and printing them:

- **Aadhaar PVC**, **Aadhaar laminated A4** (Full / Both / Small sheets)
- **PAN** (e-PAN / Instant PAN), **Voter ID** (e-EPIC), **Driving Licence**, **Vehicle RC**
- **Ration (rice) card**: from the head of family's e-Aadhaar + RC number (members from AePDS)
- **ABHA card**: the ABHA card PDF (ABHA app / abha.abdm.gov.in), front + back as PVC cards
- **Ayushman card (PM-JAY / Dr. NTR Vaidya Seva)**: the card PDF (beneficiary.nha.gov.in), front + back as PVC cards
- Printing plugins: **Epson L8050** PVC card tray, **A4 office printer** (100 % scale)

> Keep this repository **private**. It contains Microsoft fonts (Nirmala UI) and government logos
> for the shop's own use. Customer documents (`raw/`, `export/`) are never committed: see `.gitignore`.

## Set up on a new PC (Windows 10/11)

1. Install **Python 3.12** from <https://www.python.org/downloads/>.
   On the first installer screen tick **"Add python.exe to PATH"**.
2. Install **Git** from <https://git-scm.com/download/win> (default options are fine).
3. Open a terminal in the folder where you want the tool and clone it:
   ```
   git clone https://github.com/<your-account>/Print-Cards-Automate.git
   cd Print-Cards-Automate
   ```
   For a private repo, Git asks you to sign in to GitHub the first time (a browser window opens).
4. Double-click **`Setup.bat`**. It installs the Python packages from `requirements.txt`.
5. Double-click **`Start Website.bat`**. The site opens at <http://127.0.0.1:5000>.

### Settings shared by every PC

`shop_settings.json` (in git) holds the shop's shared choices: PAN front defaults (name gaps,
signature size / position / ink), the file name pattern, file type and auto-save. Every PC gets
them with `git pull`. Each PC's own `settings.json` (not in git) keeps only what that PC changed:
its save folder, network sharing and PIN, and anything set differently there. To change a shared
default for all PCs, edit `shop_settings.json`, then commit and push it.

### Printers

- **Epson L8050 (PVC cards):** install the Epson L8050 driver from Epson's website on that PC.
  The plugin's saved settings (`plugins/epson_pvc/config.json`) are the ones captured from
  Epson Photo+ (ID card tray, PVC media, A4). The printer must be named `EPSON L8050 Series`,
  or pick it again in the plugin's settings.
- **A4 office printer:** any installed Windows printer; choose it in the A4 plugin.

## Daily use

- Website: pick the card type, drop the PDFs, download / save / print.
- Batch without the website: put PDFs in `raw/` and double-click **`Make Cards.bat`**.
  Results go to `export/`.
- Aadhaar / PAN PDFs locked with a password: the file name is tried as the password
  (e.g. `SUBR1992.pdf`).
- Ration card: needs internet access to the AePDS site.
- Photo too dark / light: the sliders button on a card's preview (Adjust: brightness, contrast, colour,
  sharpness; photo only or whole card; Reset brings the original back).
- Other PCs in the shop: Settings → Network sharing (PIN + restart + `Allow Network Access.bat`),
  then open `http://<this PC's address>:5000` on the other PC.

## Updating

On the shop PC after changes are pushed:

```
git pull
```

Run `Setup.bat` again if `requirements.txt` changed.

## Not in the repository (kept only on the original PC)

| Path | Why |
|---|---|
| `raw/`, `export/` | customer documents |
| `history/` | every card made on the website (kept so the exports list survives a refresh / restart; delete cards with Remove / Clear in the website) |
| `Screenshots/` | may show customer data |
| `Printer-Resources/` | Epson installers (download them from Epson) |
| `templates/*_reference.png`, `templates/*.local.json` | a real person's card, used only to re-measure templates |

The finished templates (`templates/*.png`, `*.json`, fonts) **are** committed, so a fresh clone
works without these files.
