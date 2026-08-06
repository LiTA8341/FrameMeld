#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <string>
#include <vector>

namespace {

std::wstring quote_argument(const std::wstring& argument) {
    if (argument.empty()) return L"\"\"";
    if (argument.find_first_of(L" \t\n\v\"") == std::wstring::npos) return argument;

    std::wstring result = L"\"";
    size_t backslashes = 0;
    for (wchar_t ch : argument) {
        if (ch == L'\\') {
            ++backslashes;
        } else if (ch == L'\"') {
            result.append(backslashes * 2 + 1, L'\\');
            result.push_back(ch);
            backslashes = 0;
        } else {
            result.append(backslashes, L'\\');
            backslashes = 0;
            result.push_back(ch);
        }
    }
    result.append(backslashes * 2, L'\\');
    result.push_back(L'\"');
    return result;
}

std::wstring executable_directory() {
    std::vector<wchar_t> buffer(32768);
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length == buffer.size()) return {};
    std::wstring path(buffer.data(), length);
    const size_t separator = path.find_last_of(L"\\/");
    return separator == std::wstring::npos ? L"." : path.substr(0, separator);
}

std::wstring executable_filename() {
    std::vector<wchar_t> buffer(32768);
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length == buffer.size()) return {};
    std::wstring path(buffer.data(), length);
    const size_t separator = path.find_last_of(L"\\/");
    return separator == std::wstring::npos ? path : path.substr(separator + 1);
}

bool exists(const std::wstring& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

int fail(const std::wstring& message, int code = 2) {
    std::fwprintf(stderr, L"framemeld: %ls\n", message.c_str());
    return code;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    const std::wstring root = executable_directory();
    if (root.empty()) return fail(L"could not resolve the runtime directory");
    const bool probe_mode = _wcsicmp(executable_filename().c_str(), L"ffprobe.exe") == 0;

    std::wstring target;
    std::vector<std::wstring> child_arguments;
    int first_forwarded_argument = 1;

    const bool framemeld_mode = !probe_mode && argc > 1 &&
        (std::wstring(argv[1]) == L"-framemeld" || std::wstring(argv[1]) == L"-blur");
    if (framemeld_mode) {
        target = root + L"\\lib\\vapoursynth\\python.exe";
        child_arguments.push_back(root + L"\\tools\\framemeld_cli.py");
        first_forwarded_argument = 2;
    } else {
        target = root + (probe_mode ? L"\\lib\\ffmpeg\\ffprobe.exe" : L"\\lib\\ffmpeg\\ffmpeg-core.exe");
    }

    if (!exists(target)) return fail(L"runtime executable is missing: " + target);
    for (int index = first_forwarded_argument; index < argc; ++index) {
        child_arguments.emplace_back(argv[index]);
    }

    std::wstring command_line = quote_argument(target);
    for (const auto& argument : child_arguments) {
        command_line.push_back(L' ');
        command_line += quote_argument(argument);
    }
    std::vector<wchar_t> mutable_command(command_line.begin(), command_line.end());
    mutable_command.push_back(L'\0');

    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
            CloseHandle(job);
            job = nullptr;
        }
    }

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    const DWORD flags = job ? CREATE_SUSPENDED : 0;
    if (!CreateProcessW(
            target.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, flags,
            nullptr, nullptr, &startup, &process)) {
        if (job) CloseHandle(job);
        return fail(L"could not start child process (Windows error " + std::to_wstring(GetLastError()) + L")");
    }

    if (job) {
        if (!AssignProcessToJobObject(job, process.hProcess)) {
            TerminateProcess(process.hProcess, 2);
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
            CloseHandle(job);
            return fail(L"could not attach the child process to its cleanup job");
        }
        ResumeThread(process.hThread);
    }

    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 2;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    if (job) CloseHandle(job);
    return static_cast<int>(exit_code);
}
