$ErrorActionPreference = 'Stop'
$studioPython = Join-Path $PSScriptRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    throw 'Run setup.bat before creating the Studio shortcut.'
}
# IShellLinkW preserves Vietnamese paths; WScript.Shell uses an ANSI interface.
if (-not ('StudioShortcut' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class StudioShellLink {}
[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IStudioShellLink {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int length, IntPtr data, uint flags);
    void GetIDList(out IntPtr list);
    void SetIDList(IntPtr list);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder description, int length);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string description);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder directory, int length);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string directory);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder arguments, int length);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string arguments);
    void GetHotkey(out short hotkey);
    void SetHotkey(short hotkey);
    void GetShowCmd(out int show);
    void SetShowCmd(int show);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder icon, int length, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string icon, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
    void Resolve(IntPtr window, uint flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
}
public static class StudioShortcut {
    public static void Create(string destination, string target, string args, string root, string icon) {
        var link = (IStudioShellLink)new StudioShellLink();
        try {
            link.SetPath(target);
            link.SetArguments(args);
            link.SetWorkingDirectory(root);
            link.SetIconLocation(icon, 0);
            link.SetDescription("Douyin2TikTok AI Studio");
            link.SetShowCmd(1);
            ((IPersistFile)link).Save(destination, true);
        } finally { Marshal.FinalReleaseComObject(link); }
    }
}
'@
}
[StudioShortcut]::Create(
    (Join-Path $PSScriptRoot 'Douyin2TikTok AI Studio.lnk'),
    $studioPython,
    ('-B "' + (Join-Path $PSScriptRoot 'desktop_app.py') + '"'),
    $PSScriptRoot,
    (Join-Path $PSScriptRoot 'docs\images\studio.ico')
)
Write-Host 'Created Douyin2TikTok AI Studio shortcut in the application folder.'
