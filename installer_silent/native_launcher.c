/* native_launcher.c - the single-exe silent installer stub (NATIVE PE).
 *
 * Built by build_silent_installer.ps1, which appends a ZIP payload + a 16-byte
 * trailer to this compiled exe:
 *
 *     [ launcher.exe ][ payload.zip ][ int64 zipLength ][ magic "MCSFX001" ]
 *
 * JARVIS runs `MaterialClassification_Silent_Setup.exe -InstallDir {INSTALL_DIR}`.
 * This stub parses -InstallDir, then runs the embedded extraction script
 * (silent_extract.ps1, UTF-16 base64 substituted for @@ENC@@ at build time) via
 * `powershell -EncodedCommand`, passing paths through environment variables. It
 * waits and returns the extraction/install exit code verbatim.
 *
 * Why NATIVE and not managed (.NET): Windows cannot LAUNCH a managed exe that
 * carries a >4 GB overlay (the CLR loader chokes), but a native PE ignores the
 * overlay entirely - so an 18 GB single-file installer launches fine. The heavy
 * lifting (reading the overlay, unzipping) happens in PowerShell, which reads the
 * exe as DATA, not through the PE loader.
 *
 * No admin, no windows: launched with CREATE_NO_WINDOW.
 */
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* UTF-16 base64 of silent_extract.ps1, injected at build time. */
static const wchar_t *ENC = L"@@ENC@@";

int wmain(int argc, wchar_t **argv)
{
    const wchar_t *installDir = NULL;
    int gpu = 0;
    int i;
    for (i = 1; i < argc; i++) {
        if (_wcsicmp(argv[i], L"-InstallDir") == 0 && i + 1 < argc) {
            installDir = argv[++i];
        } else if (_wcsnicmp(argv[i], L"-InstallDir=", 12) == 0) {
            installDir = argv[i] + 12;
        } else if (_wcsicmp(argv[i], L"-Gpu") == 0) {
            gpu = 1;
        }
    }
    if (!installDir || !*installDir) {
        fwprintf(stderr, L"ERROR: -InstallDir <dir> is required.\n");
        return 2;
    }

    wchar_t self[1024];
    if (!GetModuleFileNameW(NULL, self, 1024)) {
        fwprintf(stderr, L"ERROR: GetModuleFileNameW failed (%lu)\n", GetLastError());
        return 1;
    }

    SetEnvironmentVariableW(L"MC_SELF", self);
    SetEnvironmentVariableW(L"MC_INSTALLDIR", installDir);
    SetEnvironmentVariableW(L"MC_GPU", gpu ? L"1" : L"0");

    size_t cmdlen = wcslen(ENC) + 256;
    wchar_t *cmd = (wchar_t *)malloc(cmdlen * sizeof(wchar_t));
    if (!cmd) return 1;
    _snwprintf(cmd, cmdlen,
        L"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand %s",
        ENC);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));

    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, CREATE_NO_WINDOW,
                        NULL, NULL, &si, &pi)) {
        fwprintf(stderr, L"ERROR: could not start powershell (%lu)\n", GetLastError());
        free(cmd);
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    free(cmd);
    return (int)code;
}
