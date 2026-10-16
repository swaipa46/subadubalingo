// SubaDubaLingo-mcp.exe: the MCP launcher at a path that never changes across versions,
//
//     %LOCALAPPDATA%\Programs\SubaDubaLingo\SubaDubaLingo-mcp.exe
//
// so an MCP client other than Claude desktop (Claude Code, Gemini CLI, Codex CLI, VS Code, Cursor), whose config the
// installer does not rewrite, keeps working after an update. It reads current.txt next to itself and runs
// <current>\SubaDubaLingo.exe --mcp (plus its own arguments) with the SAME standard handles, waits for it and exits
// with its exit code. It writes nothing to stdout (MCP is JSON-RPC over stdout); problems go to stderr.
//
// Why an exe and not a .cmd: Node-based clients cannot spawn a .cmd/.bat without a shell (Claude Code's docs ask for
// "cmd /c" around npx for that reason), and a .cmd opens a console window. This is a GUI-subsystem exe like the app,
// so no window appears either. The server runs in a job object that is closed (and the server killed) when the
// launcher goes, so a client that kills the launcher does not leave the server behind; children of the server (the
// app it starts) break away from the job and keep running.
//
// Built by build.ps1 with the C# compiler of .NET Framework 4 (on every Windows 10/11, no SDK):
//   csc /nologo /target:winexe /optimize+ /win32icon:assets\branding\SubaDubaLingo.ico /out:SubaDubaLingo-mcp.exe tools\mcp_launcher.cs
// installer.activate copies it from the version folder to the base folder at every install and update.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

static class Launcher
{
    const string AppExe = "SubaDubaLingo.exe";

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct STARTUPINFO
    {
        public int cb;
        public string lpReserved, lpDesktop, lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }

    [StructLayout(LayoutKind.Sequential)]
    struct BASIC_LIMIT
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct EXTENDED_LIMIT
    {
        public BASIC_LIMIT Basic;
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;   // IO_COUNTERS
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags,
                                      IntPtr env, string dir, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int which);
    [DllImport("kernel32.dll")] static extern bool SetHandleInformation(IntPtr h, uint mask, uint flags);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h, out uint code);
    [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateJobObjectW(IntPtr sa, string name);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr job, int cls, ref EXTENDED_LIMIT info, int size);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    static int Fail(string message)
    {
        try { Console.Error.WriteLine("SubaDubaLingo-mcp: " + message); } catch { }
        return 1;
    }

    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
        var sb = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in a)
        {
            if (c == '\\') { slashes++; continue; }
            sb.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            slashes = 0;
            sb.Append(c);
        }
        sb.Append('\\', slashes * 2).Append('"');
        return sb.ToString();
    }

    static int Main(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string version;
        try { version = File.ReadAllText(Path.Combine(baseDir, "current.txt")).Trim(); }
        catch (Exception e) { return Fail("SubaDubaLingo is not installed here (" + e.Message + ")"); }
        string exe = Path.Combine(Path.Combine(baseDir, version), AppExe);
        if (version.Length == 0 || !File.Exists(exe)) return Fail("missing " + exe);

        var cmd = new StringBuilder(Quote(exe)).Append(" --mcp");
        foreach (string a in args) cmd.Append(' ').Append(Quote(a));

        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        si.dwFlags = 0x100;   // STARTF_USESTDHANDLES: the client's pipes, unchanged
        si.hStdInput = GetStdHandle(-10);
        si.hStdOutput = GetStdHandle(-11);
        si.hStdError = GetStdHandle(-12);
        foreach (IntPtr h in new[] { si.hStdInput, si.hStdOutput, si.hStdError })
            if (h != IntPtr.Zero && h != new IntPtr(-1)) SetHandleInformation(h, 1, 1);   // HANDLE_FLAG_INHERIT

        IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
        if (job != IntPtr.Zero)
        {
            var info = new EXTENDED_LIMIT();
            info.Basic.LimitFlags = 0x2000 | 0x1000;   // KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK
            if (!SetInformationJobObject(job, 9, ref info, Marshal.SizeOf(typeof(EXTENDED_LIMIT)))) job = IntPtr.Zero;
        }

        PROCESS_INFORMATION pi;
        const uint CREATE_SUSPENDED = 0x4, CREATE_UNICODE_ENVIRONMENT = 0x400;
        if (!CreateProcessW(exe, cmd, IntPtr.Zero, IntPtr.Zero, true, CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT,
                            IntPtr.Zero, null, ref si, out pi))
            return Fail("could not start " + exe + " (error " + Marshal.GetLastWin32Error() + ")");
        if (job != IntPtr.Zero) AssignProcessToJobObject(job, pi.hProcess);   // best effort
        ResumeThread(pi.hThread);
        CloseHandle(pi.hThread);
        WaitForSingleObject(pi.hProcess, 0xFFFFFFFF);
        uint code;
        GetExitCodeProcess(pi.hProcess, out code);
        CloseHandle(pi.hProcess);
        return (int)code;
    }
}
