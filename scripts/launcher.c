#define UNICODE 1
#define _UNICODE 1
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, PWSTR pCmdLine, int nCmdShow) {
    wchar_t appDir[MAX_PATH];
    if (!GetModuleFileNameW(NULL, appDir, MAX_PATH)) {
        MessageBoxW(NULL, L"Không thể xác định thư mục ứng dụng.", L"Lỗi khởi động", MB_ICONERROR | MB_OK);
        return 1;
    }
    
    // Remove filename to get directory path
    wchar_t *lastSlash = wcsrchr(appDir, L'\\');
    if (lastSlash) {
        *lastSlash = L'\0';
    }
    
    // Set current working directory to application root
    SetCurrentDirectoryW(appDir);
    
    // Optimize python environment
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1");
    SetEnvironmentVariableW(L"HF_HUB_DISABLE_XET", L"1");

    // Path to pythonw.exe inside venv
    wchar_t pythonwPath[MAX_PATH];
    swprintf(pythonwPath, MAX_PATH, L"%s\\venv\\Scripts\\pythonw.exe", appDir);

    // Check if pythonw.exe exists
    DWORD attribs = GetFileAttributesW(pythonwPath);
    if (attribs == INVALID_FILE_ATTRIBUTES || (attribs & FILE_ATTRIBUTE_DIRECTORY)) {
        MessageBoxW(NULL, 
            L"Không tìm thấy môi trường Python tại venv\\Scripts\\pythonw.exe.\n\nVui lòng nhấp đúp vào setup.bat để thiết lập môi trường trước khi chạy.", 
            L"Douyin2TikTok AI Studio - Chưa cài đặt", 
            MB_ICONEXCLAMATION | MB_OK);
        return 1;
    }

    // Path to desktop_app.py
    wchar_t scriptPath[MAX_PATH];
    swprintf(scriptPath, MAX_PATH, L"%s\\desktop_app.py", appDir);

    // Parameters for pythonw.exe: -B "appDir\desktop_app.py" <pCmdLine>
    wchar_t params[4096];
    if (pCmdLine && wcslen(pCmdLine) > 0) {
        swprintf(params, 4096, L"-B \"%s\" %s", scriptPath, pCmdLine);
    } else {
        swprintf(params, 4096, L"-B \"%s\"", scriptPath);
    }

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_SHOW;
    ZeroMemory(&pi, sizeof(pi));

    wchar_t cmdLine[4096];
    swprintf(cmdLine, 4096, L"\"%s\" %s", pythonwPath, params);

    if (CreateProcessW(NULL, cmdLine, NULL, NULL, FALSE, 0, NULL, appDir, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return 0;
    }

    // Fallback to ShellExecuteExW if CreateProcessW fails
    SHELLEXECUTEINFOW sei;
    ZeroMemory(&sei, sizeof(sei));
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_NOASYNC;
    sei.lpVerb = L"open";
    sei.lpFile = pythonwPath;
    sei.lpParameters = params;
    sei.lpDirectory = appDir;
    sei.nShow = SW_SHOWNORMAL;

    if (!ShellExecuteExW(&sei)) {
        wchar_t errorMsg[512];
        swprintf(errorMsg, 512, L"Không thể khởi chạy ứng dụng (Mã lỗi: %lu).\nVui lòng kiểm tra lại thiết lập.", GetLastError());
        MessageBoxW(NULL, errorMsg, L"Lỗi khởi động", MB_ICONERROR | MB_OK);
        return 1;
    }

    return 0;
}
