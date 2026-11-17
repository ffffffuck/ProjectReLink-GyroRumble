/* The exefs patch files of the mods folder and their hash, the same as nx2/exefs_mods.py's files_hash (and the launcher's
   ExefsMods.cs): mods.c compares it with the first line of relink_exefs_mods.txt, which the build wrote, to tell whether the
   game was built with the code patches the folder has now. Patches are applied when the game is built (the code is
   translated ahead of time), so here they are only found and hashed.

   The mods are found as mods.c finds them, in the layout emulators use: any folder below <mods> (four levels at most)
   holding a romfs or exefs folder is a mod and is not looked into further; folders whose names start with '.', '-' or '_'
   are skipped. A mod's patch files are the files directly in its exefs folder whose names end in .ips or .pchtxt (case
   aside).

   The hash: FNV-1a 64 over, for each file in the order of its relative path "<mod>/exefs/<file>" ('/', UTF-8) compared with
   ASCII letters in lower case (then as it is), the path's bytes, a zero byte, the file's size as 8 bytes little-endian, and
   the file's bytes (a file that cannot be read counts as empty).

   No hos.h here: nx2/test_exefs_mods.py builds this file alone, with a test main, and checks it against the Python, both
   ways: Windows' wide-character calls, and the POSIX ones Linux uses (-DMODS_EXEFS_POSIX: MinGW's dirent.h on Windows). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32) && !defined(MODS_EXEFS_POSIX)
#define MODS_EXEFS_WIN32 1
#else
#define MODS_EXEFS_WIN32 0
#endif
#if MODS_EXEFS_WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
typedef wchar_t pchar;
#define P(s) L##s
#else
#include <dirent.h>
#include <sys/stat.h>
typedef char pchar;
#define P(s) s
#endif

#define EXEFS_MAX_DEPTH 4
#define PATH_CAP 2048

typedef struct { char *rel; pchar *path; } PatchFile;
typedef struct { PatchFile *v; int n, cap; } PatchList;

static void list_add(PatchList *l, const char *rel, const pchar *path) {
    if (l->n == l->cap) {
        l->cap = l->cap ? l->cap * 2 : 16;
        l->v = (PatchFile *)realloc(l->v, sizeof *l->v * (size_t)l->cap);
    }
    l->v[l->n].rel = (char *)malloc(strlen(rel) + 1);
    strcpy(l->v[l->n].rel, rel);
#if MODS_EXEFS_WIN32
    l->v[l->n].path = (pchar *)malloc((wcslen(path) + 1) * sizeof(pchar));
    wcscpy(l->v[l->n].path, path);
#else
    l->v[l->n].path = (pchar *)malloc(strlen(path) + 1);
    strcpy(l->v[l->n].path, path);
#endif
    l->n++;
}

static unsigned char fold(unsigned char c) { return c >= 'A' && c <= 'Z' ? (unsigned char)(c + 32) : c; }

static int rel_cmp(const void *a, const void *b) {
    const unsigned char *x = (const unsigned char *)((const PatchFile *)a)->rel, *y = (const unsigned char *)((const PatchFile *)b)->rel;
    for (size_t i = 0;; i++) {
        const unsigned char cx = fold(x[i]), cy = fold(y[i]);
        if (cx != cy) return cx < cy ? -1 : 1;
        if (!cx) break;
    }
    return strcmp((const char *)x, (const char *)y);
}

static int patch_name(const char *name) {   /* (UTF-8) ends in .ips or .pchtxt, case aside */
    const size_t n = strlen(name);
    const char *ext[] = {".ips", ".pchtxt"};
    for (int k = 0; k < 2; k++) {
        const size_t e = strlen(ext[k]);
        if (n < e) continue;
        size_t i = 0;
        while (i < e && fold((unsigned char)name[n - e + i]) == (unsigned char)ext[k][i]) i++;
        if (i == e) return 1;
    }
    return 0;
}

#if MODS_EXEFS_WIN32
static int exists(const pchar *p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }
static void utf8(const wchar_t *w, char *out, int cap) {
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, cap, NULL, NULL)) out[0] = 0;
}

static void mod_files(PatchList *l, const pchar *dir, const char *name) {
    pchar pat[PATH_CAP];
    _snwprintf(pat, PATH_CAP, L"%ls\\exefs\\*", dir);
    pat[PATH_CAP - 1] = 0;
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW(pat, &fd);
    if (f == INVALID_HANDLE_VALUE) return;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        char file[PATH_CAP], rel[PATH_CAP * 2];
        utf8(fd.cFileName, file, sizeof file);
        if (!patch_name(file)) continue;
        snprintf(rel, sizeof rel, "%s/exefs/%s", name, file);
        pchar path[PATH_CAP];
        _snwprintf(path, PATH_CAP, L"%ls\\exefs\\%ls", dir, fd.cFileName);
        path[PATH_CAP - 1] = 0;
        list_add(l, rel, path);
    } while (FindNextFileW(f, &fd));
    FindClose(f);
}

static void walk(PatchList *l, const pchar *dir, const char *name, int depth) {
    pchar probe[PATH_CAP], probe2[PATH_CAP];
    _snwprintf(probe, PATH_CAP, L"%ls\\romfs", dir);
    _snwprintf(probe2, PATH_CAP, L"%ls\\exefs", dir);
    probe[PATH_CAP - 1] = probe2[PATH_CAP - 1] = 0;
    if (depth > 0 && (exists(probe) || exists(probe2))) {
        mod_files(l, dir, name);
        return;
    }
    if (depth >= EXEFS_MAX_DEPTH) return;
    pchar pat[PATH_CAP];
    _snwprintf(pat, PATH_CAP, L"%ls\\*", dir);
    pat[PATH_CAP - 1] = 0;
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW(pat, &fd);
    if (f == INVALID_HANDLE_VALUE) return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.' || fd.cFileName[0] == L'-' ||
            fd.cFileName[0] == L'_')
            continue;
        char sub_name[PATH_CAP * 2], part[PATH_CAP];
        utf8(fd.cFileName, part, sizeof part);
        snprintf(sub_name, sizeof sub_name, "%s%s%s", name, *name ? "/" : "", part);
        pchar sub[PATH_CAP];
        _snwprintf(sub, PATH_CAP, L"%ls\\%ls", dir, fd.cFileName);
        sub[PATH_CAP - 1] = 0;
        walk(l, sub, sub_name, depth + 1);
    } while (FindNextFileW(f, &fd));
    FindClose(f);
}

static FILE *open_read(const pchar *p) { return _wfopen(p, L"rb"); }
#else
static int exists(const pchar *p) { struct stat st; return stat(p, &st) == 0; }
static int is_dir(const char *p) { struct stat st; return stat(p, &st) == 0 && S_ISDIR(st.st_mode); }

static void mod_files(PatchList *l, const pchar *dir, const char *name) {
    char exefs[PATH_CAP];
    snprintf(exefs, sizeof exefs, "%s/exefs", dir);
    DIR *d = opendir(exefs);
    if (!d) return;
    for (struct dirent *e; (e = readdir(d));) {
        char path[PATH_CAP], rel[PATH_CAP * 2];
        snprintf(path, sizeof path, "%s/%s", exefs, e->d_name);
        if (is_dir(path) || !patch_name(e->d_name)) continue;
        snprintf(rel, sizeof rel, "%s/exefs/%s", name, e->d_name);
        list_add(l, rel, path);
    }
    closedir(d);
}

static void walk(PatchList *l, const pchar *dir, const char *name, int depth) {
    char probe[PATH_CAP], probe2[PATH_CAP];
    snprintf(probe, sizeof probe, "%s/romfs", dir);
    snprintf(probe2, sizeof probe2, "%s/exefs", dir);
    if (depth > 0 && (exists(probe) || exists(probe2))) {
        mod_files(l, dir, name);
        return;
    }
    if (depth >= EXEFS_MAX_DEPTH) return;
    DIR *d = opendir(dir);
    if (!d) return;
    for (struct dirent *e; (e = readdir(d));) {
        if (e->d_name[0] == '.' || e->d_name[0] == '-' || e->d_name[0] == '_') continue;
        char sub[PATH_CAP], sub_name[PATH_CAP * 2];
        snprintf(sub, sizeof sub, "%s/%s", dir, e->d_name);
        if (!is_dir(sub)) continue;
        snprintf(sub_name, sizeof sub_name, "%s%s%s", name, *name ? "/" : "", e->d_name);
        walk(l, sub, sub_name, depth + 1);
    }
    closedir(d);
}

static FILE *open_read(const pchar *p) { return fopen(p, "rb"); }
#endif

static uint64_t fnv(uint64_t h, const void *data, size_t n) {
    const unsigned char *p = (const unsigned char *)data;
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
    return h;
}

/* The exefs patch files under root (a UTF-8 path) and their hash (*hash; untouched without a file). Returns how many, 0 for
   none (the report's "none"). each(arg, rel, size), when given, is called for each file in the hash's order */
int hos_exefs_patch_hash(const char *root, uint64_t *hash, void (*each)(void *arg, const char *rel, uint64_t size), void *arg) {
    PatchList l = {0};
#if MODS_EXEFS_WIN32
    pchar wroot[PATH_CAP];
    if (!MultiByteToWideChar(CP_UTF8, 0, root, -1, wroot, PATH_CAP)) return 0;
    if (!exists(wroot)) return 0;
    walk(&l, wroot, "", 0);
#else
    if (!exists(root)) return 0;
    walk(&l, root, "", 0);
#endif
    if (l.n) qsort(l.v, (size_t)l.n, sizeof *l.v, rel_cmp);
    uint64_t h = 0xcbf29ce484222325ull;
    for (int i = 0; i < l.n; i++) {
        unsigned char *data = NULL;
        size_t size = 0;
        FILE *f = open_read(l.v[i].path);
        if (f) {
            size_t cap = 0;
            for (;;) {
                if (size == cap) {
                    cap = cap ? cap * 2 : 65536;
                    unsigned char *grown = (unsigned char *)realloc(data, cap);
                    if (!grown) { size = 0; break; }
                    data = grown;
                }
                const size_t got = fread(data + size, 1, cap - size, f);
                if (!got) break;
                size += got;
            }
            fclose(f);
        }
        unsigned char len[8];
        for (int k = 0; k < 8; k++) len[k] = (unsigned char)((uint64_t)size >> (8 * k));
        h = fnv(h, l.v[i].rel, strlen(l.v[i].rel) + 1);   /* (the path and its zero byte) */
        h = fnv(h, len, 8);
        h = fnv(h, data, size);
        if (each) each(arg, l.v[i].rel, size);
        free(data);
        free(l.v[i].rel);
        free(l.v[i].path);
    }
    free(l.v);
    if (l.n) *hash = h;
    return l.n;
}
