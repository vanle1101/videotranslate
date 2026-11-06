import os
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path

import shutil

public_icon = Path("C:/Users/Public/Douyin2TikTok_studio.ico")
shutil.copy2("docs/images/studio.ico", str(public_icon))

exe_path = str(Path('E:/Dịch video/Douyin2TikTok.exe').resolve())
icon_path = str(public_icon.resolve())
working_dir = str(Path('E:/Dịch video').resolve())

target_desktops = []
p1 = Path(os.environ['USERPROFILE']) / 'OneDrive' / 'Desktop'
if p1.exists():
    target_desktops.append(p1)
p2 = Path(os.environ['USERPROFILE']) / 'Desktop'
if p2.exists() and p2 not in target_desktops:
    target_desktops.append(p2)

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8)
    ]

    def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
        self.Data1 = l
        self.Data2 = w1
        self.Data3 = w2
        self.Data4 = (wintypes.BYTE * 8)(b1, b2, b3, b4, b5, b6, b7, b8)

CLSID_ShellLink = GUID(0x00021401, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)
IID_IShellLinkW = GUID(0x000214F9, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)
IID_IPersistFile = GUID(0x0000010b, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)

ole32 = ctypes.oledll.ole32
ole32.CoInitialize(None)

class IShellLinkWVtbl(ctypes.Structure):
    pass

class IShellLinkW(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(IShellLinkWVtbl))]

IShellLinkWVtbl._fields_ = [
    ("QueryInterface", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
    ("AddRef", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
    ("Release", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
    ("GetPath", ctypes.c_void_p),
    ("GetIDList", ctypes.c_void_p),
    ("SetIDList", ctypes.c_void_p),
    ("GetDescription", ctypes.c_void_p),
    ("SetDescription", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)),
    ("GetWorkingDirectory", ctypes.c_void_p),
    ("SetWorkingDirectory", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)),
    ("GetArguments", ctypes.c_void_p),
    ("SetArguments", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)),
    ("GetHotkey", ctypes.c_void_p),
    ("SetHotkey", ctypes.c_void_p),
    ("GetShowCmd", ctypes.c_void_p),
    ("SetShowCmd", ctypes.c_void_p),
    ("GetIconLocation", ctypes.c_void_p),
    ("SetIconLocation", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int)),
    ("SetRelativePath", ctypes.c_void_p),
    ("Resolve", ctypes.c_void_p),
    ("SetPath", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR)),
]

class IPersistFileVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
        ("AddRef", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
        ("Release", ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)),
        ("GetClassID", ctypes.c_void_p),
        ("IsDirty", ctypes.c_void_p),
        ("Load", ctypes.c_void_p),
        ("Save", ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.BOOL)),
        ("SaveCompleted", ctypes.c_void_p),
        ("GetCurFile", ctypes.c_void_p),
    ]

class IPersistFile(ctypes.Structure):
    _fields_ = [("lpVtbl", ctypes.POINTER(IPersistFileVtbl))]

for desktop in target_desktops:
    lnk_path = str(desktop / 'Douyin2TikTok AI Studio.lnk')
    p_shell_link = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink),
        None,
        1, # CLSCTX_INPROC_SERVER
        ctypes.byref(IID_IShellLinkW),
        ctypes.byref(p_shell_link)
    )
    if hr != 0:
        print(f"CoCreateInstance failed: {hr:#x}")
        sys.exit(1)

    shell_link = ctypes.cast(p_shell_link, ctypes.POINTER(IShellLinkW))
    vtbl = shell_link.contents.lpVtbl.contents

    vtbl.SetPath(p_shell_link, exe_path)
    vtbl.SetWorkingDirectory(p_shell_link, working_dir)
    vtbl.SetIconLocation(p_shell_link, icon_path, 0)
    vtbl.SetDescription(p_shell_link, "Douyin2TikTok AI Studio")

    p_persist_file = ctypes.c_void_p()
    hr = vtbl.QueryInterface(p_shell_link, ctypes.byref(IID_IPersistFile), ctypes.byref(p_persist_file))
    if hr != 0:
        print(f"QueryInterface for IPersistFile failed: {hr:#x}")
        sys.exit(1)

    persist_file = ctypes.cast(p_persist_file, ctypes.POINTER(IPersistFile))
    hr = persist_file.contents.lpVtbl.contents.Save(p_persist_file, lnk_path, True)
    if hr != 0:
        print(f"Save shortcut failed: {hr:#x}")
        sys.exit(1)

    persist_file.contents.lpVtbl.contents.Release(p_persist_file)
    vtbl.Release(p_shell_link)
    print("SUCCESS: Shortcut created with icon at:", lnk_path)

ole32.CoUninitialize()

# Also refresh Windows icon cache
ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x1000, None, None) # SHCNE_ASSOCCHANGED | SHCNF_FLUSH
print("Notified Windows shell of icon change.")
