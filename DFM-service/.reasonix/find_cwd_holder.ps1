$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class PwdFinder {
    [DllImport("ntdll.dll")]
    public static extern int NtQueryInformationProcess(IntPtr hProcess, int pic, ref PROCESS_BASIC_INFORMATION pbi, int cb, out int pSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr OpenProcess(int access, bool inherit, int pid);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, IntPtr size, out IntPtr read);

    [DllImport("kernel32.dll")]
    public static extern bool CloseHandle(IntPtr h);

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_BASIC_INFORMATION {
        public IntPtr Reserved1;
        public IntPtr PebBaseAddress;
        public IntPtr Reserved2_0;
        public IntPtr Reserved2_1;
        public IntPtr UniqueProcessId;
        public IntPtr Reserved3;
    }

    // Reads the CWD string of a process: PEB -> ProcessParameters -> CurrentDirectory.DosPath
    public static string GetCwd(int pid) {
        IntPtr h = OpenProcess(0x1000 /*QUERY_LIMITED*/ | 0x0010 /*VM_READ*/, false, pid);
        if (h == IntPtr.Zero) return null;
        try {
            PROCESS_BASIC_INFORMATION pbi = new PROCESS_BASIC_INFORMATION();
            int sz;
            int st = NtQueryInformationProcess(h, 0, ref pbi, Marshal.SizeOf(typeof(PROCESS_BASIC_INFORMATION)), out sz);
            if (st != 0) return null;

            // ProcessParameters pointer at PEB+0x20 (x64)
            byte[] buf = new byte[8];
            IntPtr read;
            if (!ReadProcessMemory(h, (IntPtr)((long)pbi.PebBaseAddress + 0x20), buf, (IntPtr)8, out read)) return null;
            IntPtr paramsAddr = (IntPtr)BitConverter.ToInt64(buf, 0);
            if (paramsAddr == IntPtr.Zero) return null;

            // RTL_USER_PROCESS_PARAMETERS: CurrentDirectory (UNICODE_STRING curDir + HANDLE handle) at offset 0x38
            // UNICODE_STRING: len(2) maxLen(2) pad(4) buffer(8) => read the DosPath UNICODE_STRING at params+0x38
            byte[] us = new byte[16];
            if (!ReadProcessMemory(h, (IntPtr)((long)paramsAddr + 0x38), us, (IntPtr)16, out read)) return null;
            short len = BitConverter.ToInt16(us, 0);
            IntPtr bufAddr = (IntPtr)BitConverter.ToInt64(us, 8);
            if (len <= 0 || len > 1024) return null;
            byte[] str = new byte[len];
            if (!ReadProcessMemory(h, bufAddr, str, (IntPtr)len, out read)) return null;
            return System.Text.Encoding.Unicode.GetString(str);
        } finally {
            CloseHandle(h);
        }
    }
}
"@

$results = @()
foreach ($p in Get-Process) {
    try {
        $cwd = [PwdFinder]::GetCwd($p.Id)
        if ($cwd -and $cwd -like "*DFM-service*") {
            $results += [pscustomobject]@{ PID = $p.Id; Name = $p.Name; CWD = $cwd }
        }
    } catch {}
}
if ($results.Count -eq 0) { "NO process has CWD under DFM-service" } else { $results | Format-Table -AutoSize }
