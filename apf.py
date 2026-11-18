"""
Print look - the Epson PhotoEnhance "Landscape" look, reproduced in software.

Why: the shop prints PVC cards through Epson Photo+ with Auto Correction = PhotoEnhance (scene
"Landscape"). That look is what they expect on the card, but letting the printer apply it makes the
colour depend on the printer/driver and drift. So we bake the same look into the image here and print
with the printer's Auto Correction OFF - same result every time, on any output path.

How it was derived: Epson "Landscape" = APF (Auto Photo Fine!) mode "scenery", level 5. It is not in the
.etdx project file (that only stores the mode name) and can't be read back from the print spool. It was
measured by calibrating against Epson Photo+'s own on-screen output (see apf_calib.json): auto-crop the
card from a screenshot, align it to the true original, and fit the colour change. The finding: on an ID
card APF is mostly an ADAPTIVE auto-levels (brighten + clean the flat/scanned look), with a touch LESS
saturation - the scene modes (Landscape/Standard/People/Night) are nearly identical on document cards.

So the look here is adaptive auto-levels (suits each card, never over-brightens an already-bright one)
followed by the measured colour character. Toggle: settings "print_look".
"""
import numpy as np
from PIL import Image, ImageEnhance

# Measured from Epson "Landscape" (APF scenery) vs adaptive auto-levels on a real card:
_GAIN = (0.986, 0.975, 0.993)       # per-channel gain after auto-levels (almost none)
_SAT = 0.916                         # Landscape sits a little BELOW raw auto-levels saturation


def _auto_levels(a, lo=0.5, hi=99.5):
    """Per-channel percentile stretch - adaptive, so a flat/scanned card is lifted and a card that is
    already full-range is left alone."""
    a = a.astype(np.float32)
    out = a.copy()
    for c in range(3):
        l, h = np.percentile(a[..., c], (lo, hi))
        if h - l >= 25:                 # skip a channel with almost no range (don't amplify noise)
            out[..., c] = (a[..., c] - l) * (255.0 / (h - l))
    return np.clip(out, 0, 255)


def landscape_look(img):
    """PIL RGB in -> PIL RGB out: the Landscape print look."""
    a = _auto_levels(np.asarray(img.convert("RGB")))
    a = np.clip(a * np.asarray(_GAIN, np.float32), 0, 255).astype(np.uint8)
    return ImageEnhance.Color(Image.fromarray(a)).enhance(_SAT)
