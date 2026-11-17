using System.IO;
using System.Text;

namespace ReLink;

/// Exefs mods: the code patches (.ips, .pchtxt) in the game folder's mods. The game's code is translated when the game is
/// built, so they take effect only then: the build writes relink_exefs_mods.txt, whose first line is the hash of the patch
/// files it saw ("none" for none), and the Home page offers a build when the files there now hash differently. The mods
/// and the hash are the same as nx2/exefs_mods.py's and host/mods_exefs.c's: any folder below mods (four levels at most)
/// holding a romfs or exefs folder is a mod; folders whose names start with '.', '-' or '_' are skipped; a mod's patch
/// files are those directly in its exefs folder ending in .ips or .pchtxt; FNV-1a 64 over each file's relative path
/// "<mod>/exefs/<file>" (UTF-8, sorted by its bytes with ASCII letters in lower case, then as they are), a zero byte, its
/// size as 8 bytes little-endian and its bytes.
static class ExefsMods
{
    const int MaxDepth = 4;
    public const string Report = "relink_exefs_mods.txt";

    static readonly byte[] Zero = { 0 };
    static readonly EnumerationOptions All =new() { AttributesToSkip = 0, IgnoreInaccessible = true, RecurseSubdirectories = false };

    /// Where the game looks for mods: HOS_MODS, else the folder's mods
    public static string Root(string folder) =>
        Environment.GetEnvironmentVariable("HOS_MODS") is { Length: > 0 } env ? env : Path.Combine(folder, "mods");

    static bool Exists(string path) => File.Exists(path) || Directory.Exists(path);

    static void Walk(List<(string Rel, string Path)> files, string dir, string name, int depth)
    {
        if (depth > 0 && (Exists(Path.Combine(dir, "romfs")) || Exists(Path.Combine(dir, "exefs"))))
        {
            try
            {
                foreach (var f in new DirectoryInfo(Path.Combine(dir, "exefs")).EnumerateFiles("*", All))
                    if (f.Name.EndsWith(".ips", StringComparison.OrdinalIgnoreCase) || f.Name.EndsWith(".pchtxt", StringComparison.OrdinalIgnoreCase))
                        files.Add(($"{name}/exefs/{f.Name}", f.FullName));
            }
            catch (Exception) { }
            return;
        }
        if (depth >= MaxDepth) return;
        IEnumerable<DirectoryInfo> subs;
        try { subs = new DirectoryInfo(dir).EnumerateDirectories("*", All).ToList(); }
        catch (Exception) { return; }
        foreach (var d in subs)
        {
            if (d.Name.Length == 0 || d.Name[0] is '.' or '-' or '_') continue;
            Walk(files, d.FullName, name.Length > 0 ? $"{name}/{d.Name}" : d.Name, depth + 1);
        }
    }

    static byte Fold(byte c) => c is >= (byte)'A' and <= (byte)'Z' ? (byte)(c + 32) : c;

    static int Compare(byte[] a, byte[] b)
    {
        for (var i = 0; ; i++)
        {
            int x = i < a.Length ? Fold(a[i]) : -1, y = i < b.Length ? Fold(b[i]) : -1;
            if (x != y) return x < y ? -1 : 1;
            if (x < 0) break;
        }
        for (var i = 0; ; i++)
        {
            int x = i < a.Length ? a[i] : -1, y = i < b.Length ? b[i] : -1;
            if (x != y) return x < y ? -1 : 1;
            if (x < 0) return 0;
        }
    }

    /// The patch files under root and their hash: "%016x", or "none" without one
    public static (string Hash, int Count) Present(string root)
    {
        var found = new List<(string Rel, string Path)>();
        if (Directory.Exists(root)) Walk(found, root, "", 0);
        if (found.Count == 0) return ("none", 0);
        var files = found.Select(f => (Rel: Encoding.UTF8.GetBytes(f.Rel), f.Path)).ToList();
        files.Sort((a, b) => Compare(a.Rel, b.Rel));
        var h = 0xcbf29ce484222325UL;
        void Add(ReadOnlySpan<byte> data)
        {
            foreach (var c in data) h = unchecked((h ^ c) * 0x100000001b3UL);
        }
        foreach (var (rel, path) in files)
        {
            byte[] data;
            try { data = File.ReadAllBytes(path); }
            catch (Exception) { data = Array.Empty<byte>(); }   // (as the game and the build: a file that cannot be read is empty)
            Add(rel);
            Add(Zero);
            Add(BitConverter.GetBytes((ulong)data.LongLength));   // (little-endian on every PC .NET runs this launcher on)
            Add(data);
        }
        return (h.ToString("x16"), files.Count);
    }

    /// The hash of the patch files the folder's game was built with; null when the build wrote none (a folder built before
    /// exefs mods were read)
    public static string? Built(string folder)
    {
        try
        {
            using var r = new StreamReader(Path.Combine(folder, Report));
            return r.ReadLine()?.Trim();
        }
        catch (Exception) { return null; }
    }

    /// The patch files in mods now are not the ones the game was built with (count: how many there are now)
    public static bool Changed(string folder, out int count)
    {
        var (now, n) = Present(Root(folder));
        count = n;
        var built = Built(folder);
        return built == null ? n > 0 : built != now;
    }
}
