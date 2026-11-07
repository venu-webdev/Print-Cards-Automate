# Shop PC test checklist

Bring: a ruler (mm), 2–3 blank PVC cards, a few A4 sheets, one real PVC card for comparison,
and a few customer PDFs (Aadhaar, PAN, DL, Voter ID, RC, plus an RC number for a ration card).

## 0. Install / update

- [ ] Python 3.12 installed with **"Add python.exe to PATH"** ticked
- [ ] Git installed, signed in to GitHub as **venu-webdev**
- [ ] `git clone https://github.com/venu-webdev/Print-Cards-Automate.git` (or `git pull` if already cloned)
- [ ] `Setup.bat` finished without errors
- [ ] `Start Website.bat` opens <http://127.0.0.1:5000>
- [ ] Epson L8050 driver installed; printer shows as **EPSON L8050 Series** in Windows
- [ ] Canon iR printer installed in Windows and prints a normal Windows test page

## 1. Make cards (no printing yet)

For each type, upload a PDF and check the preview looks right:

- [ ] Aadhaar PVC (front + back, photo, QR)
- [ ] Aadhaar laminated A4 (Full / Both / Small, green tick + time)
- [ ] PAN (back shows the silver hologram, no grey corners)
- [ ] Driving Licence, Voter ID, Vehicle RC
- [ ] Ration card (needs internet for AePDS; check Telugu names in the ⋯ panel)

## 2. Canon iR: A4 at 100%

Settings (gear) → **A4 office printing** → choose the Canon → **Print scale test page**.

- [ ] 100 × 100 mm square measures **exactly 100 mm** both ways
- [ ] 150 mm ruler measures 150 mm
- [ ] A real PVC card fits the card outline (85.6 × 54 mm)
- [ ] Note which corner marks are missing (that's only the printer margin, it's OK)

If the square is smaller or bigger: in Windows **Printers → Canon → Printing preferences**, turn off
"Fit to page" / "Scale" / "Enlarge/Reduce" and print the test again.

- [ ] Print one Aadhaar laminated **Full** sheet; cut and check the card is the right size

## 3. Epson L8050: PVC card

Settings (gear) → **Epson PVC card printing**.

- [ ] Status shows the printer as ready
- [ ] Put a **blank card** in the card tray, then **Print alignment test**
- [ ] The outline lands on the card edges, and the **TOP EDGE** arrow points the right way
      (if it's upside down, switch rotation to 270°)
- [ ] If it's shifted, set **Move right / down** (mm; negative = left / up) and test again
- [ ] Print a real card (e.g. Aadhaar front), then the back on the other side of the same card
- [ ] Colours and photo brightness look right; no white edge (if there is, raise bleed a little)

Write down any values you changed (offsets, scale, rotation, bleed); they're saved on that PC.

## If something goes wrong

- Keep the website window open; the black window shows the error text. Take a photo or screenshot of it.
- "Print Spooler is not running": Windows Services → **Print Spooler** → Start.
- Wrong printer chosen: pick it again in Settings.
