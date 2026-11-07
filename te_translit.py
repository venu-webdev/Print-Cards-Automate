"""
English -> Telugu transliteration for Indian / Telugu personal names (offline, rule based).

Not a dictionary: names are spelled phonetically the way they are usually written in Telugu
(Venu -> వేణు is close: వేను). Known words (e.g. the family surname from the head of family's
Aadhaar, which UIDAI prints in both scripts) are taken from `known` first, so shared names are exact.
"""
import re

VIRAMA = "్"
# consonant clusters first (longest match wins)
CONS = [
    ("ksh", "క్ష"), ("chh", "ఛ"), ("thr", "త్ర"), ("shr", "శ్ర"), ("sri", None), ("sree", None),
    ("ch", "చ"), ("th", "త"), ("dh", "ధ"), ("bh", "భ"), ("gh", "ఘ"), ("jh", "ఝ"), ("kh", "ఖ"),
    ("ph", "ఫ"), ("nd", None), ("sh", "శ"), ("tt", "ట్ట"), ("dd", "డ్డ"), ("ll", "ల్ల"), ("nn", "న్న"), ("mm", "మ్మ"),
    ("pp", "ప్ప"), ("kk", "క్క"), ("ss", "స్స"), ("yy", "య్య"), ("rr", "ర్ర"), ("cc", "చ్చ"), ("jj", "జ్జ"),
    ("gg", "గ్గ"), ("bb", "బ్బ"), ("vv", "వ్వ"), ("k", "క"), ("g", "గ"), ("c", "క"), ("j", "జ"),
    ("t", "ట"), ("d", "ద"), ("n", "న"), ("p", "ప"), ("b", "బ"), ("m", "మ"), ("y", "య"), ("r", "ర"),
    ("l", "ల"), ("v", "వ"), ("w", "వ"), ("s", "స"), ("h", "హ"), ("f", "ఫ"), ("z", "జ"), ("q", "క"),
    ("x", "క్స"),
]
# vowel spelling -> (independent letter, sign after a consonant)
VOW = [
    ("aa", ("ఆ", "ా")), ("ee", ("ఈ", "ీ")), ("ii", ("ఈ", "ీ")), ("oo", ("ఊ", "ూ")), ("uu", ("ఊ", "ూ")),
    ("ai", ("ఐ", "ై")), ("au", ("ఔ", "ౌ")), ("ou", ("ఔ", "ౌ")), ("a", ("అ", "")), ("i", ("ఇ", "ి")),
    ("u", ("ఉ", "ు")), ("e", ("ఏ", "ే")), ("o", ("ఓ", "ో")),
]
SHORT = {"ే": "ె", "ో": "ొ", "ఏ": "ఎ", "ఓ": "ఒ"}
INITIALS = {"a": "ఎ", "b": "బి", "c": "సి", "d": "డి", "e": "ఇ", "f": "ఎఫ్", "g": "జి", "h": "హెచ్",
            "i": "ఐ", "j": "జె", "k": "కే", "l": "ఎల్", "m": "ఎం", "n": "ఎన్", "o": "ఒ", "p": "పి",
            "q": "క్యూ", "r": "ఆర్", "s": "ఎస్", "t": "టి", "u": "యు", "v": "వి", "w": "డబ్ల్యు",
            "x": "ఎక్స్", "y": "వై", "z": "జెడ్"}
SPECIAL = {"sri": "శ్రీ", "sree": "శ్రీ", "shri": "శ్రీ", "reddy": "రెడ్డి", "naidu": "నాయుడు",
           "lakshmi": "లక్ష్మి", "krishna": "కృష్ణ", "venkata": "వెంకట", "venkatesh": "వెంకటేష్",
           "rao": "రావు", "kumar": "కుమార్", "kumari": "కుమారి", "devi": "దేవి", "babu": "బాబు",
           "rani": "రాణి", "venu": "వేణు", "uma": "ఉమ", "siva": "శివ", "shiva": "శివ",
           "radha": "రాధ", "radhamma": "రాధమ్మ", "balaji": "బాలాజీ", "srinivasulu": "శ్రీనివాసులు",
           "srinivas": "శ్రీనివాస్", "srinivasa": "శ్రీనివాస", "damodar": "దామోదర్", "damodhar": "దామోదర్",
           "subramani": "సుబ్రమణి", "subramanyam": "సుబ్రహ్మణ్యం", "ramana": "రమణ", "raju": "రాజు",
           "raja": "రాజా", "ramu": "రాము", "rama": "రామ", "ramaiah": "రామయ్య", "narayana": "నారాయణ",
           "narasimha": "నరసింహ", "anjaneyulu": "ఆంజనేయులు", "anjali": "అంజలి", "prasad": "ప్రసాద్",
           "nagaraju": "నాగరాజు", "nagamma": "నాగమ్మ", "naga": "నాగ", "nagini": "నాగిని",
           "gopal": "గోపాల్", "mohan": "మోహన్", "madhavi": "మాధవి", "sai": "సాయి", "kiran": "కిరణ్",
           "kalyani": "కళ్యాణి", "padma": "పద్మ", "pushpa": "పుష్ప", "santhosh": "సంతోష్",
           "suresh": "సురేష్", "mahesh": "మహేష్", "naresh": "నరేష్", "rajesh": "రాజేష్", "ganesh": "గణేష్",
           "vani": "వాణి", "sarojamma": "సరోజమ్మ", "lakshmamma": "లక్ష్మమ్మ", "reddeppa": "రెడ్డెప్ప",
           "reddemma": "రెడ్డెమ్మ", "chinna": "చిన్న", "peddanna": "పెద్దన్న", "devamma": "దేవమ్మ",
           "kumari": "కుమారి", "bai": "బాయి", "begum": "బేగం", "khan": "ఖాన్", "basha": "బాషా",
           "shaik": "షేక్", "shaikh": "షేక్", "mohammed": "మహమ్మద్", "mohammad": "మహమ్మద్", "sekhar": "శేఖర్",
           "shekar": "శేఖర్", "sekar": "శేఖర్", "chandra": "చంద్ర", "chinnaya": "చిన్నయ"}
CONSONANT_START = "kgcjtdnpbmyrlvwshfzqx"


def _word(w):
    w = w.lower()
    if not w:
        return ""
    if w in SPECIAL:
        return SPECIAL[w]
    if len(w) == 1:
        return INITIALS.get(w, w)
    w = re.sub(r"aiah$|aih$|aya$", "ayya", w)               # Kondaiah -> కొండయ్య
    w = re.sub(r"(?<=[^aeiou])iah$", "iyya", w)
    out, i, pending = [], 0, None           # pending = consonant waiting for its vowel
    while i < len(w):
        # sri / sree inside a word
        for sp in ("sree", "sri", "shri"):
            if w.startswith(sp, i) and (i + len(sp) == len(w) or w[i + len(sp)] not in "aeiou"):
                if pending:
                    out.append(pending + VIRAMA)
                out.append("శ్రీ"); pending = None; i += len(sp)
                break
        else:
            v = next(((s, t) for s, t in VOW if w.startswith(s, i)), None)
            if v:
                s, (ind, sign) = v
                nxt = w[i + len(s):i + len(s) + 2]
                # e/o before a double consonant or 'nk/nd/nt' are short (Venkata, Reddy, Bommi)
                if s in ("e", "o") and (len(nxt) == 2 and (nxt[0] == nxt[1] or nxt in ("nk", "nd", "nt", "ng"))):
                    ind, sign = SHORT.get(ind, ind), SHORT.get(sign, sign)
                # final 'y' after a consonant acts like 'i' (Reddy, Bhavya keeps ya)
                if pending:
                    out.append(pending + sign); pending = None
                else:
                    out.append(ind)
                i += len(s)
                continue
            # final y -> i sound (Reddy)
            if w[i] == "y" and i == len(w) - 1 and pending:
                out.append(pending + "ి"); pending = None; i += 1
                continue
            if w.startswith("nd", i) and out and w[i + 2:i + 3] != "r":          # Konda -> కొండ (dental-retroflex in Telugu)
                if pending:
                    out.append(pending); pending = None
                out.append("ం"); pending = "డ"; i += 2
                continue
            c = next(((s, t) for s, t in CONS if t and w.startswith(s, i)), None)
            if not c:
                i += 1
                continue
            s, t = c
            nxt = w[i + len(s):i + len(s) + 1]
            # n / m before another consonant -> anusvara  (Jaswanth -> జస్వంత్, Sampath -> సంపత్)
            if s in ("n", "m") and nxt and nxt in CONSONANT_START and nxt not in "yvrnm" and pending is None and out:
                out.append("ం"); i += 1
                continue
            if s == "sh" and (i + 2 == len(w) or w[i + 2:i + 3] in ("p", "t", "k", "n", "m")):
                t = "ష"                       # Ramesh -> రమేష్, Pushpa -> పుష్ప
            if pending:
                out.append(pending + VIRAMA)
            pending = t; i += len(s)
    if pending:
        out.append(pending + VIRAMA)
    return "".join(out)


def name(text, known=None):
    """Transliterate a full name; words found in `known` (lower-case english -> telugu) win."""
    known = {k.lower(): v for k, v in (known or {}).items()}
    words = re.findall(r"[A-Za-z]+", text or "")
    return " ".join(known.get(w.lower()) or _word(w) for w in words)


def learn(english, telugu):
    """Word pairs from a name printed in both scripts (e.g. Aadhaar). Same word count only."""
    e = re.findall(r"[A-Za-z]+", english or "")
    t = (telugu or "").split()
    return {a.lower(): b for a, b in zip(e, t)} if len(e) == len(t) and e else {}
