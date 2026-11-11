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
- [ ] ABHA card (`ABHA-Card-….pdf`): front + back, no grey line at the edges, back text not cut off
- [ ] DL / RC **back**: ~3 mm clear border, no text at the edges (print one on PVC and check)
- [ ] PAN front: signature slightly smaller than before, still above "Signature"
- [ ] Adjust a card (sliders button): photo-only brightness changes only the photo; Save; Reset gives the original back

## 1b. Another PC (network sharing)

- [ ] Main PC: Settings → Network sharing → PIN → switch on → restart `Start Website.bat` → Allow through firewall
- [ ] Other PC: open the address shown, enter the PIN, see the cards, print a card (prints on the main PC)
- [ ] Wrong PIN is refused; changing the PIN on the main PC logs the other PC out
- [ ] Ration card (needs internet for AePDS; check Telugu names in the ⋯ panel)
- [ ] Ration card with EPDS: Open EPDS rice card search → Submit → Ctrl+A, Ctrl+C → paste → "✓ … with date of birth";
      the card's member table shows the real dates of birth (not 01-01)
- [ ] Refresh the page (F5), then close and restart `Start Website.bat`: all cards are still in the list,
      with their date, typed file names and "Saved" tick
- [ ] Remove one card (⋯ → Remove): it asks first, then it's gone after a refresh too

## 2. Canon iR: A4 at 100%

Settings (gear) → **A4 office printing** → choose the Canon → **Print scale test page**.

- [ ] 100 × 100 mm square measures **exactly 100 mm** both ways
- [ ] 150 mm ruler measures 150 mm
- [ ] A real PVC card fits the card outline (85.6 × 54 mm)
- [ ] Note which corner marks are missing (that's only the printer margin, it's OK)

If the square is smaller or bigger: in Windows **Printers → Canon → Printing preferences**, turn off
"Fit to page" / "Scale" / "Enlarge/Reduce" and print the test again.

- [ ] Print one Aadhaar laminated **Full** sheet from the **Multi-purpose Tray** (default); cut and check the card is the right size
- [ ] Pick a **Drawer** in the Paper tray list and check the sheet comes from that drawer
- [ ] Tick 2–3 laminated cards → **Print A4** on the bar at the bottom: all their sheets print in one job
- [ ] **Small sheet row by row:** laminated Aadhaar → Print → *Small sheet – row by row* → **Print now in row 1**.
      Put the **same sheet** back the same way round, do the next customer → it lands in **row 2**, not on top of row 1
      (note which way round the Canon wants the sheet re-fed; the 5th row must not be cut off at the bottom)
- [ ] *Add to sheet* for 2 customers, then **Small sheet** (top bar) → **Print waiting rows**: both print in one pass

## 3. Epson L8050: PVC card

Settings (gear) → **Epson PVC card printing**.

- [ ] Status shows the printer as ready
- [ ] Put a **blank card** in the card tray, then **Print alignment test**
- [ ] The outline lands on the card edges, and the **TOP EDGE** arrow points the right way
      (if it's upside down, switch rotation to 270°)
- [ ] If it's shifted, set **Move right / down** (mm; negative = left / up) and test again
- [ ] Blank cards in **both slots**, then **Test both slots**: each outline lands on its own card
      (if only slot 2 is off, set **Slot 2: move right / down**)
- [ ] Print a real card (e.g. Aadhaar front), then the back on the other side of the same card
- [ ] Print → **Same card ×2**: front + back on two cards in one go
- [ ] Tick **two different cards** in the list → **Print on PVC** on the bar at the bottom
      (first ticked = slot 1, **Swap slots** if needed); after the fronts, flip each card in its own slot
- [ ] Colours and photo brightness look right; no white edge (if there is, raise bleed a little)

Write down any values you changed (offsets, scale, rotation, bleed); they're saved on that PC.

## If something goes wrong

- Keep the website window open; the black window shows the error text. Take a photo or screenshot of it.
- "Print Spooler is not running": Windows Services → **Print Spooler** → Start.
- Wrong printer chosen: pick it again in Settings.
