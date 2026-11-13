"""
Capture a printer's full settings (including Canon-private ones like High Definition Text, Glossy paper,
Borderless) and re-apply them later - so the shop can set each print type up from the app, once.

Windows keeps the driver-specific settings inside a binary DEVMODE (its tail, dmDriverExtra, is private to
the driver). The standard API can't build those fields, but it CAN:
  - open the driver's own Preferences dialog and hand back the resulting DEVMODE  -> prompt_capture()
  - snapshot the printer's current default DEVMODE                                -> default_devmode()
  - print with a saved DEVMODE as the base, overriding only paper/size/copies     -> make_print_dc()
We store that DEVMODE (the whole blob) and reuse it; the private part rides along untouched.

Windows only. Uses ctypes so the full blob (with dmDriverExtra) is preserved - pywin32's PyDEVMODE drops it.
"""
import ctypes
from ctypes import wintypes

CCHDEVICENAME = 32
CCHFORMNAME = 32
# DocumentProperties fMode flags
DM_UPDATE, DM_COPY, DM_PROMPT, DM_MODIFY = 1, 2, 4, 8
DM_IN_BUFFER, DM_IN_PROMPT, DM_OUT_BUFFER = DM_MODIFY, DM_PROMPT, DM_COPY
# dmFields bits
DM_ORIENTATION, DM_PAPERSIZE, DM_PAPERLENGTH, DM_PAPERWIDTH = 0x1, 0x2, 0x4, 0x8
DM_COPIES, DM_DEFAULTSOURCE, DM_COLOR, DM_DUPLEX = 0x100, 0x200, 0x800, 0x1000
IDOK = 1

try:
    _winspool = ctypes.WinDLL("winspool.drv")
    _gdi = ctypes.WinDLL("gdi32")
    HAVE = True
except Exception:
    HAVE = False


class DEVMODE(ctypes.Structure):
    """The public head of DEVMODEW (the driver-private tail follows it in memory)."""
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * CCHDEVICENAME),
        ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short), ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short), ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short), ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short), ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * CCHFORMNAME),
        ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
        ("dmNup", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD),
    ]


def _sig():
    _winspool.OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    _winspool.OpenPrinterW.restype = wintypes.BOOL
    _winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    _winspool.DocumentPropertiesW.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.LPCWSTR,
                                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    _winspool.DocumentPropertiesW.restype = wintypes.LONG
    _gdi.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    _gdi.CreateDCW.restype = wintypes.HDC


if HAVE:
    _sig()


def _open(printer):
    h = wintypes.HANDLE()
    if not _winspool.OpenPrinterW(printer, ctypes.byref(h), None):
        raise OSError(f"Cannot open printer {printer!r}")
    return h


def _buf_size(h, printer):
    n = _winspool.DocumentPropertiesW(None, h, printer, None, None, 0)
    if n < 0:
        raise OSError("DocumentProperties size query failed")
    return n


def default_devmode(printer):
    """The printer's current default settings, as the full DEVMODE blob (bytes)."""
    h = _open(printer)
    try:
        n = _buf_size(h, printer)
        buf = ctypes.create_string_buffer(n)
        if _winspool.DocumentPropertiesW(None, h, printer, buf, None, DM_OUT_BUFFER) < 0:
            raise OSError("DocumentProperties (out buffer) failed")
        return bytes(buf.raw[:n])
    finally:
        _winspool.ClosePrinter(h)


def prompt_capture(printer, seed=None, hwnd=0):
    """Open the driver's own Preferences dialog (seeded with `seed` blob or the current default) and return
    the chosen settings as a DEVMODE blob, or None if the user pressed Cancel."""
    h = _open(printer)
    try:
        n = _buf_size(h, printer)
        out = ctypes.create_string_buffer(n)
        mode = DM_IN_PROMPT | DM_OUT_BUFFER
        inp = None
        if seed and len(seed) >= ctypes.sizeof(DEVMODE):
            inp = ctypes.create_string_buffer(seed, len(seed))
            mode |= DM_IN_BUFFER
        r = _winspool.DocumentPropertiesW(hwnd, h, printer, out, inp, mode)
        if r != IDOK:
            return None                      # Cancel (or an error) - keep the old profile
        return bytes(out.raw[:n])
    finally:
        _winspool.ClosePrinter(h)


def summary(blob):
    """A few human-readable fields from a saved blob (the private bits can't be read, only the public ones)."""
    if not blob or len(blob) < ctypes.sizeof(DEVMODE):
        return {}
    dm = DEVMODE.from_buffer_copy(blob[:ctypes.sizeof(DEVMODE)])
    col = {1: "Black & white", 2: "Colour"}.get(dm.dmColor)
    orient = {1: "Portrait", 2: "Landscape"}.get(dm.dmOrientation)
    return {"paper_code": dm.dmPaperSize, "form": dm.dmFormName or None, "orientation": orient,
            "colour": col, "copies": dm.dmCopies or 1, "media": dm.dmMediaType,
            "driver_extra": dm.dmDriverExtra, "size": dm.dmSize + dm.dmDriverExtra}


def _apply_overrides(blob, paper_code=None, orientation=None, copies=None):
    """A copy of blob with a few public fields overridden (private tail untouched)."""
    buf = ctypes.create_string_buffer(blob, len(blob))
    dm = DEVMODE.from_buffer(buf)
    if paper_code is not None:
        dm.dmPaperSize = int(paper_code); dm.dmFields |= DM_PAPERSIZE
    if orientation is not None:
        dm.dmOrientation = int(orientation); dm.dmFields |= DM_ORIENTATION
    if copies is not None:
        dm.dmCopies = max(1, int(copies)); dm.dmFields |= DM_COPIES
    return buf


def make_print_dc(printer, blob, paper_code=None, orientation=None, copies=None):
    """A printer HDC (int) using the saved settings blob, with the given overrides. Wrap it with
    win32ui.CreateDCFromHandle(hdc). Returns (hdc, applied DEVMODE buffer kept alive)."""
    buf = _apply_overrides(blob, paper_code, orientation, copies)
    hdc = _gdi.CreateDCW("WINSPOOL", printer, None, buf)
    if not hdc:
        raise OSError("CreateDC with the saved printer settings failed")
    return int(hdc), buf      # keep buf referenced until the DC is done
