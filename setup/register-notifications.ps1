# Lets Madison Desktop show calendar suggestions as Windows notifications, for the current user
# only: it adds a "Madison Desktop" Start menu shortcut, which Windows needs to know who sends the
# notifications and to pass their button clicks back (opening it refreshes the wallpaper).
# No admin rights needed.
#
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File setup\register-notifications.ps1
# Remove them again with:       powershell -ExecutionPolicy Bypass -File setup\register-notifications.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$shortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Madison Desktop.lnk"
# earlier versions registered these; Windows wouldn't pass button clicks on through them
$old = "HKCU:\Software\Classes\AppUserModelId\MadisonDesktop", "HKCU:\Software\Classes\madison-desktop"

if ($Remove) {
    Remove-Item $shortcut -ErrorAction SilentlyContinue
    Remove-Item $old -Recurse -ErrorAction SilentlyContinue
    Write-Host "Removed the Madison Desktop notifications."
    exit 0
}

if (-not (Test-Path $pythonw)) {
    Write-Host "Run setup\install.ps1 first (no .venv found)."
    exit 1
}

# A shortcut carrying the app ID the notifications are sent under (System.AppUserModel.ID).
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder file, int max, IntPtr data, uint flags);
    void GetIDList(out IntPtr list);
    void SetIDList(IntPtr list);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder name, int max);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder dir, int max);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder args, int max);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
    void GetHotkey(out short key);
    void SetHotkey(short key);
    void GetShowCmd(out int cmd);
    void SetShowCmd(int cmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int max, out int index);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
    void Resolve(IntPtr hwnd, uint flags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
}

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    void GetCount(out uint count);
    void GetAt(uint index, out PropertyKey key);
    void GetValue(ref PropertyKey key, out PropVariant value);
    void SetValue(ref PropertyKey key, ref PropVariant value);
    void Commit();
}

[ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPersistFile {
    void GetClassID(out Guid id);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string file, uint mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string file, bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string file);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string file);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
struct PropertyKey { public Guid FormatId; public uint PropertyId; }

[StructLayout(LayoutKind.Explicit)]
struct PropVariant { [FieldOffset(0)] public ushort Type; [FieldOffset(8)] public IntPtr Value; }

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
class ShellLink { }

public static class MadisonShortcut {
    public static void Create(string path, string target, string args, string dir, string icon, string appId) {
        var link = (IShellLinkW)new ShellLink();
        link.SetPath(target);
        link.SetArguments(args);
        link.SetWorkingDirectory(dir);
        link.SetIconLocation(icon, 0);
        link.SetDescription("Refresh the Madison Desktop wallpaper");
        var key = new PropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5 };
        var value = new PropVariant { Type = 31, Value = Marshal.StringToCoTaskMemUni(appId) };  // VT_LPWSTR
        try {
            var store = (IPropertyStore)link;
            store.SetValue(ref key, ref value);
            store.Commit();
            ((IPersistFile)link).Save(path, true);
        } finally {
            Marshal.FreeCoTaskMem(value.Value);
        }
    }
}
"@

Remove-Item $old -Recurse -ErrorAction SilentlyContinue
[MadisonShortcut]::Create($shortcut, $pythonw, "fetch.py", $root, (Join-Path $PSScriptRoot "icon.ico"), "MadisonDesktop")

Write-Host "Done. Try it with:  .venv\Scripts\python suggest.py --test"
