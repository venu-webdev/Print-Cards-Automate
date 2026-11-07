"""
Windows Print Ticket helpers (documented API: prntvpt.dll).

Driver-specific options such as Epson's print quality and PhotoEnhance scene correction are
not plain DEVMODE fields, but the driver publishes them as Print Schema features. We read the
driver's PrintCapabilities XML and convert a small Print Ticket into a DEVMODE the driver
understands - the same mechanism any modern Windows app uses.
"""
import ctypes
import re
from ctypes import wintypes

prntvpt = ctypes.WinDLL("prntvpt.dll")
ole32 = ctypes.WinDLL("ole32.dll")
kernel32 = ctypes.WinDLL("kernel32.dll")

HPTPROVIDER = ctypes.c_void_p
prntvpt.PTOpenProvider.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(HPTPROVIDER)]
prntvpt.PTCloseProvider.argtypes = [HPTPROVIDER]
prntvpt.PTGetPrintCapabilities.argtypes = [HPTPROVIDER, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
prntvpt.PTConvertDevModeToPrintTicket.argtypes = [HPTPROVIDER, wintypes.ULONG, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
prntvpt.PTConvertPrintTicketToDevMode.argtypes = [HPTPROVIDER, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                                  ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(ctypes.c_void_p),
                                                  ctypes.POINTER(ctypes.c_void_p)]
prntvpt.PTMergeAndValidatePrintTicket.argtypes = [HPTPROVIDER, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                                  ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
prntvpt.PTReleaseMemory.argtypes = [ctypes.c_void_p]
ole32.CreateStreamOnHGlobal.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.POINTER(ctypes.c_void_p)]
ole32.GetHGlobalFromStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p

kPTJobScope = 2
kUserDefaultDevmode = 0


def _release(unk):
    if unk:
        vtbl = ctypes.cast(unk, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])(unk)


def _new_stream(data=b""):
    hg = None
    if data:
        hg = kernel32.GlobalAlloc(0x0002, len(data))          # GMEM_MOVEABLE
        p = kernel32.GlobalLock(hg)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(hg)
    s = ctypes.c_void_p()
    hr = ole32.CreateStreamOnHGlobal(hg, True, ctypes.byref(s))
    if hr:
        raise OSError(f"CreateStreamOnHGlobal failed {hr:#x}")
    return s


def _stream_bytes(s):
    hg = ctypes.c_void_p()
    ole32.GetHGlobalFromStream(s, ctypes.byref(hg))
    n = kernel32.GlobalSize(hg)
    p = kernel32.GlobalLock(hg)
    try:
        return ctypes.string_at(p, n)
    finally:
        kernel32.GlobalUnlock(hg)


class Provider:
    def __init__(self, printer):
        self.h = HPTPROVIDER()
        hr = prntvpt.PTOpenProvider(printer, 1, ctypes.byref(self.h))
        if hr:
            raise OSError(f"PTOpenProvider failed {hr & 0xFFFFFFFF:#x}")

    def close(self):
        prntvpt.PTCloseProvider(self.h)

    def capabilities(self, ticket_xml=None):
        out = _new_stream()
        inp = _new_stream(ticket_xml) if ticket_xml else None
        try:
            hr = prntvpt.PTGetPrintCapabilities(self.h, inp, out, None)
            if hr:
                raise OSError(f"PTGetPrintCapabilities failed {hr & 0xFFFFFFFF:#x}")
            return _stream_bytes(out).rstrip(b"\x00")
        finally:
            _release(out); _release(inp)

    def devmode_to_ticket(self, devmode_bytes):
        out = _new_stream()
        buf = ctypes.create_string_buffer(devmode_bytes)
        try:
            hr = prntvpt.PTConvertDevModeToPrintTicket(self.h, len(devmode_bytes), buf, kPTJobScope, out)
            if hr:
                raise OSError(f"PTConvertDevModeToPrintTicket failed {hr & 0xFFFFFFFF:#x}")
            return _stream_bytes(out).rstrip(b"\x00")
        finally:
            _release(out)

    def ticket_to_devmode(self, ticket_xml):
        inp = _new_stream(ticket_xml)
        size, dm, err = wintypes.ULONG(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            hr = prntvpt.PTConvertPrintTicketToDevMode(self.h, inp, kUserDefaultDevmode, kPTJobScope,
                                                        ctypes.byref(size), ctypes.byref(dm), ctypes.byref(err))
            if hr:
                raise OSError(f"PTConvertPrintTicketToDevMode failed {hr & 0xFFFFFFFF:#x}")
            data = ctypes.string_at(dm, size.value)
            prntvpt.PTReleaseMemory(dm)
            return data
        finally:
            _release(inp)


def features(caps_xml):
    """{feature name: [option names]} from a PrintCapabilities document."""
    t = caps_xml.decode("utf-8", "ignore")
    out = {}
    for fm in re.finditer(r'<psf:Feature name="([^"]+)"(.*?)</psf:Feature>', t, re.S):
        opts = re.findall(r'<psf:Option name="([^"]+)"', fm.group(2))
        out[fm.group(1)] = opts
    return out
