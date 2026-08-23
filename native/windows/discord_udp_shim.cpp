#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <winver.h>

#include <MinHook.h>

#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t kDiscoveryPacketSize = 74;
constexpr std::size_t kMaxVoicePacketSize = 65507;
constexpr unsigned int kDefaultDelayMs = 50;

HMODULE g_module = nullptr;
HMODULE g_system_version = nullptr;
INIT_ONCE g_version_once = INIT_ONCE_STATIC_INIT;
bool g_voice_enabled = true;
unsigned int g_voice_delay_ms = kDefaultDelayMs;
std::wstring g_voice_packet_file;

using SendFn = int(WSAAPI *)(SOCKET, const char *, int, int);
using SendToFn = int(WSAAPI *)(SOCKET, const char *, int, int, const sockaddr *, int);
using WSASendFn = int(WSAAPI *)(SOCKET, LPWSABUF, DWORD, LPDWORD, DWORD,
                                LPWSAOVERLAPPED, LPWSAOVERLAPPED_COMPLETION_ROUTINE);
using WSASendToFn = int(WSAAPI *)(SOCKET, LPWSABUF, DWORD, LPDWORD, DWORD,
                                  const sockaddr *, int, LPWSAOVERLAPPED,
                                  LPWSAOVERLAPPED_COMPLETION_ROUTINE);
using CloseSocketFn = int(WSAAPI *)(SOCKET);

SendFn g_real_send = nullptr;
SendToFn g_real_sendto = nullptr;
WSASendFn g_real_wsa_send = nullptr;
WSASendToFn g_real_wsa_sendto = nullptr;
CloseSocketFn g_real_close_socket = nullptr;

struct PeerKey {
    SOCKET socket;
    std::vector<std::uint8_t> destination;

    bool operator==(const PeerKey &other) const noexcept {
        return socket == other.socket && destination == other.destination;
    }
};

struct PeerKeyHash {
    std::size_t operator()(const PeerKey &key) const noexcept {
        std::size_t hash = std::hash<std::uintptr_t>{}(
            static_cast<std::uintptr_t>(key.socket));
        for (const auto byte : key.destination) {
            hash ^= static_cast<std::size_t>(byte) + 0x9e3779b9U + (hash << 6U) +
                    (hash >> 2U);
        }
        return hash;
    }
};

std::mutex g_peer_mutex;
std::unordered_set<PeerKey, PeerKeyHash> g_primed_peers;

BOOL CALLBACK load_system_version(PINIT_ONCE, PVOID, PVOID *) {
    std::array<wchar_t, MAX_PATH> directory{};
    const UINT length = GetSystemDirectoryW(directory.data(),
                                             static_cast<UINT>(directory.size()));
    if (length == 0 || length + 13 >= directory.size()) {
        return FALSE;
    }
    std::wstring path(directory.data(), length);
    path += L"\\version.dll";
    g_system_version = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (g_system_version == nullptr) {
        g_system_version = LoadLibraryW(path.c_str());
    }
    return g_system_version != nullptr;
}

template <typename Function>
Function version_function(const char *name) {
    InitOnceExecuteOnce(&g_version_once, load_system_version, nullptr, nullptr);
    if (g_system_version == nullptr) {
        SetLastError(ERROR_MOD_NOT_FOUND);
        return nullptr;
    }
    const auto procedure = GetProcAddress(g_system_version, name);
    if (procedure == nullptr) {
        SetLastError(ERROR_PROC_NOT_FOUND);
        return nullptr;
    }
    return reinterpret_cast<Function>(procedure);
}

bool parse_bool(const wchar_t *value, bool fallback) {
    if (value == nullptr || *value == L'\0') {
        return fallback;
    }
    if (_wcsicmp(value, L"0") == 0 || _wcsicmp(value, L"false") == 0 ||
        _wcsicmp(value, L"no") == 0 || _wcsicmp(value, L"off") == 0) {
        return false;
    }
    if (_wcsicmp(value, L"1") == 0 || _wcsicmp(value, L"true") == 0 ||
        _wcsicmp(value, L"yes") == 0 || _wcsicmp(value, L"on") == 0) {
        return true;
    }
    return fallback;
}

unsigned int parse_delay(const wchar_t *value, unsigned int fallback) {
    if (value == nullptr || *value == L'\0') {
        return fallback;
    }
    wchar_t *end = nullptr;
    const unsigned long parsed = wcstoul(value, &end, 10);
    if (end == value || *end != L'\0' || parsed > 1000UL) {
        return fallback;
    }
    return static_cast<unsigned int>(parsed);
}

std::wstring module_directory() {
    std::vector<wchar_t> path(32768);
    const DWORD length = GetModuleFileNameW(g_module, path.data(),
                                             static_cast<DWORD>(path.size()));
    if (length == 0 || length >= path.size()) {
        return {};
    }
    std::wstring result(path.data(), length);
    const auto separator = result.find_last_of(L"\\/");
    if (separator == std::wstring::npos) {
        return {};
    }
    result.resize(separator + 1);
    return result;
}

std::wstring adjacent_config_path() {
    std::wstring result = module_directory();
    if (!result.empty()) {
        result += L"discord-local-proxy.ini";
    }
    return result;
}

bool absolute_windows_path(const std::wstring &path) {
    return path.size() >= 2 &&
           (path[0] == L'\\' || path[0] == L'/' || path[1] == L':');
}

std::wstring packet_path_from_value(const std::wstring &value) {
    if (value.empty() || absolute_windows_path(value)) {
        return value;
    }
    return module_directory() + value;
}

std::wstring default_packet_path() {
    const std::wstring directory = module_directory();
    if (directory.empty()) {
        return {};
    }
    const std::wstring primary = directory + L"discord-local-proxy-packet.bin";
    if (GetFileAttributesW(primary.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return primary;
    }
    return directory + L"drover-packet.bin";
}

void load_voice_settings() {
    g_voice_packet_file = default_packet_path();
    const std::wstring config_path = adjacent_config_path();
    if (!config_path.empty()) {
        std::array<wchar_t, 32> ini_enabled{};
        GetPrivateProfileStringW(L"voice", L"enabled", L"true", ini_enabled.data(),
                                 static_cast<DWORD>(ini_enabled.size()),
                                 config_path.c_str());
        g_voice_enabled = parse_bool(ini_enabled.data(), true);
        const UINT delay = GetPrivateProfileIntW(L"voice", L"delay_ms",
                                                  kDefaultDelayMs,
                                                  config_path.c_str());
        g_voice_delay_ms = delay <= 1000U ? delay : kDefaultDelayMs;
        std::vector<wchar_t> packet_value(32768);
        const DWORD packet_length = GetPrivateProfileStringW(
            L"voice", L"packet_file", L"", packet_value.data(),
            static_cast<DWORD>(packet_value.size()), config_path.c_str());
        if (packet_length > 0 && packet_length < packet_value.size() - 1) {
            g_voice_packet_file = packet_path_from_value(
                std::wstring(packet_value.data(), packet_length));
        }
    }

    std::array<wchar_t, 32> value{};
    DWORD length = GetEnvironmentVariableW(L"DISCORD_LOCAL_PROXY_VOICE_ENABLED",
                                            value.data(),
                                            static_cast<DWORD>(value.size()));
    if (length > 0 && length < value.size()) {
        g_voice_enabled = parse_bool(value.data(), g_voice_enabled);
    }
    value.fill(L'\0');
    length = GetEnvironmentVariableW(L"DISCORD_LOCAL_PROXY_VOICE_DELAY_MS",
                                     value.data(),
                                     static_cast<DWORD>(value.size()));
    if (length > 0 && length < value.size()) {
        g_voice_delay_ms = parse_delay(value.data(), g_voice_delay_ms);
    }
    std::vector<wchar_t> packet_environment(32768);
    length = GetEnvironmentVariableW(
        L"DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE", packet_environment.data(),
        static_cast<DWORD>(packet_environment.size()));
    if (length > 0 && length < packet_environment.size()) {
        g_voice_packet_file.assign(packet_environment.data(), length);
    }
}

bool is_udp_socket(SOCKET socket) {
    int socket_type = 0;
    int length = sizeof(socket_type);
    return getsockopt(socket, SOL_SOCKET, SO_TYPE,
                      reinterpret_cast<char *>(&socket_type), &length) == 0 &&
           socket_type == SOCK_DGRAM;
}

bool is_discovery_packet(const std::array<std::uint8_t, 4> &signature,
                         std::size_t length) {
    return length == kDiscoveryPacketSize && signature[0] == 0x00 &&
           signature[1] == 0x01 && signature[2] == 0x00 &&
           signature[3] == 0x46;
}

std::optional<std::vector<std::uint8_t>> destination_for(
    SOCKET socket, const sockaddr *provided, int provided_length) {
    sockaddr_storage storage{};
    int length = provided_length;
    if (provided != nullptr && provided_length > 0 &&
        static_cast<std::size_t>(provided_length) <= sizeof(storage)) {
        std::memcpy(&storage, provided, static_cast<std::size_t>(provided_length));
    } else {
        length = sizeof(storage);
        if (getpeername(socket, reinterpret_cast<sockaddr *>(&storage), &length) != 0 ||
            length <= 0 || static_cast<std::size_t>(length) > sizeof(storage)) {
            return std::nullopt;
        }
    }
    const auto *begin = reinterpret_cast<const std::uint8_t *>(&storage);
    return std::vector<std::uint8_t>(begin, begin + length);
}

bool mark_first_peer(SOCKET socket, std::vector<std::uint8_t> destination) {
    std::lock_guard lock(g_peer_mutex);
    return g_primed_peers.insert(PeerKey{socket, std::move(destination)}).second;
}

void forget_socket(SOCKET socket) {
    std::lock_guard lock(g_peer_mutex);
    for (auto iterator = g_primed_peers.begin(); iterator != g_primed_peers.end();) {
        if (iterator->socket == socket) {
            iterator = g_primed_peers.erase(iterator);
        } else {
            ++iterator;
        }
    }
}

void send_custom_packet(SOCKET socket, const sockaddr *destination,
                        int destination_length) {
    if (g_voice_packet_file.empty()) {
        return;
    }
    const HANDLE file = CreateFileW(
        g_voice_packet_file.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return;
    }
    BY_HANDLE_FILE_INFORMATION info{};
    const bool valid = GetFileInformationByHandle(file, &info) != FALSE &&
                       (info.dwFileAttributes &
                        (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) == 0 &&
                       info.nFileSizeHigh == 0 && info.nFileSizeLow >= 1 &&
                       info.nFileSizeLow <= kMaxVoicePacketSize;
    if (!valid) {
        CloseHandle(file);
        return;
    }
    std::vector<char> data(info.nFileSizeLow);
    DWORD offset = 0;
    while (offset < info.nFileSizeLow) {
        DWORD amount = 0;
        if (ReadFile(file, data.data() + offset, info.nFileSizeLow - offset,
                     &amount, nullptr) == FALSE || amount == 0) {
            break;
        }
        offset += amount;
    }
    CloseHandle(file);
    if (offset != info.nFileSizeLow) {
        return;
    }
    if (destination != nullptr && destination_length > 0 &&
        g_real_sendto != nullptr) {
        (void)g_real_sendto(socket, data.data(), static_cast<int>(data.size()), 0,
                            destination, destination_length);
    } else if (g_real_send != nullptr) {
        (void)g_real_send(socket, data.data(), static_cast<int>(data.size()), 0);
    }
}

void prime_socket(SOCKET socket, int flags, const sockaddr *destination,
                  int destination_length) {
    constexpr char zero = 0x00;
    constexpr char one = 0x01;
    (void)flags;
    send_custom_packet(socket, destination, destination_length);
    if (destination != nullptr && destination_length > 0) {
        if (g_real_sendto == nullptr) {
            return;
        }
        (void)g_real_sendto(socket, &zero, 1, 0, destination, destination_length);
        (void)g_real_sendto(socket, &one, 1, 0, destination, destination_length);
    } else if (g_real_send != nullptr) {
        (void)g_real_send(socket, &zero, 1, 0);
        (void)g_real_send(socket, &one, 1, 0);
    } else {
        return;
    }
    if (g_voice_delay_ms > 0) {
        Sleep(g_voice_delay_ms);
    }
}

void maybe_prime(SOCKET socket, const std::array<std::uint8_t, 4> &signature,
                 std::size_t length, int flags, const sockaddr *destination,
                 int destination_length) {
    if (!g_voice_enabled || !is_discovery_packet(signature, length) ||
        !is_udp_socket(socket)) {
        return;
    }
    auto peer = destination_for(socket, destination, destination_length);
    if (!peer.has_value() || !mark_first_peer(socket, std::move(*peer))) {
        return;
    }
    prime_socket(socket, flags, destination, destination_length);
}

int WSAAPI hooked_sendto(SOCKET socket, const char *buffer, int length, int flags,
                         const sockaddr *destination, int destination_length) {
    if (buffer != nullptr && length >= 4) {
        std::array<std::uint8_t, 4> signature{};
        std::memcpy(signature.data(), buffer, signature.size());
        maybe_prime(socket, signature, static_cast<std::size_t>(length), flags,
                    destination, destination_length);
    }
    return g_real_sendto(socket, buffer, length, flags, destination,
                         destination_length);
}

int WSAAPI hooked_send(SOCKET socket, const char *buffer, int length, int flags) {
    if (buffer != nullptr && length >= 4) {
        std::array<std::uint8_t, 4> signature{};
        std::memcpy(signature.data(), buffer, signature.size());
        maybe_prime(socket, signature, static_cast<std::size_t>(length), flags,
                    nullptr, 0);
    }
    return g_real_send(socket, buffer, length, flags);
}

bool gather_buffers(const WSABUF *buffers, DWORD count,
                    std::array<std::uint8_t, 4> &signature,
                    std::size_t &total) {
    std::size_t copied = 0;
    total = 0;
    for (DWORD index = 0; index < count; ++index) {
        total += buffers[index].len;
        if (buffers[index].buf != nullptr && copied < signature.size()) {
            const std::size_t available = buffers[index].len;
            const std::size_t wanted = signature.size() - copied;
            const std::size_t amount = available < wanted ? available : wanted;
            std::memcpy(signature.data() + copied, buffers[index].buf, amount);
            copied += amount;
        }
    }
    return copied == signature.size();
}

int WSAAPI hooked_wsa_sendto(SOCKET socket, LPWSABUF buffers, DWORD count,
                             LPDWORD sent, DWORD flags,
                             const sockaddr *destination, int destination_length,
                             LPWSAOVERLAPPED overlapped,
                             LPWSAOVERLAPPED_COMPLETION_ROUTINE completion) {
    std::array<std::uint8_t, 4> signature{};
    std::size_t total = 0;
    if (buffers != nullptr && gather_buffers(buffers, count, signature, total)) {
        maybe_prime(socket, signature, total, static_cast<int>(flags), destination,
                    destination_length);
    }
    return g_real_wsa_sendto(socket, buffers, count, sent, flags, destination,
                             destination_length, overlapped, completion);
}

int WSAAPI hooked_wsa_send(SOCKET socket, LPWSABUF buffers, DWORD count,
                           LPDWORD sent, DWORD flags, LPWSAOVERLAPPED overlapped,
                           LPWSAOVERLAPPED_COMPLETION_ROUTINE completion) {
    std::array<std::uint8_t, 4> signature{};
    std::size_t total = 0;
    if (buffers != nullptr && gather_buffers(buffers, count, signature, total)) {
        maybe_prime(socket, signature, total, static_cast<int>(flags), nullptr, 0);
    }
    return g_real_wsa_send(socket, buffers, count, sent, flags, overlapped,
                           completion);
}

int WSAAPI hooked_close_socket(SOCKET socket) {
    forget_socket(socket);
    return g_real_close_socket(socket);
}

template <typename Function>
bool create_hook(const char *name, void *hook, Function *original) {
    return MH_CreateHookApi(L"ws2_32.dll", name, hook,
                            reinterpret_cast<void **>(original)) == MH_OK;
}

DWORD WINAPI install_hooks(void *) {
    load_voice_settings();
    if (LoadLibraryW(L"ws2_32.dll") == nullptr || MH_Initialize() != MH_OK) {
        return 1;
    }
    const bool send_hook = create_hook(
        "send", reinterpret_cast<void *>(&hooked_send), &g_real_send);
    const bool sendto_hook = create_hook(
        "sendto", reinterpret_cast<void *>(&hooked_sendto), &g_real_sendto);
    const bool wsa_send_hook = create_hook(
        "WSASend", reinterpret_cast<void *>(&hooked_wsa_send), &g_real_wsa_send);
    const bool wsa_sendto_hook = create_hook(
        "WSASendTo", reinterpret_cast<void *>(&hooked_wsa_sendto),
        &g_real_wsa_sendto);
    const bool close_hook = create_hook(
        "closesocket", reinterpret_cast<void *>(&hooked_close_socket),
        &g_real_close_socket);
    if (!send_hook || !sendto_hook || !wsa_send_hook || !wsa_sendto_hook ||
        !close_hook || MH_EnableHook(MH_ALL_HOOKS) != MH_OK) {
        MH_Uninitialize();
        return 2;
    }
    return 0;
}

}  // namespace

extern "C" BOOL WINAPI GetFileVersionInfoA(LPCSTR file, DWORD handle, DWORD length,
                                             LPVOID data) {
    using Function = decltype(&GetFileVersionInfoA);
    const auto function = version_function<Function>("GetFileVersionInfoA");
    return function ? function(file, handle, length, data) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoW(LPCWSTR file, DWORD handle, DWORD length,
                                             LPVOID data) {
    using Function = decltype(&GetFileVersionInfoW);
    const auto function = version_function<Function>("GetFileVersionInfoW");
    return function ? function(file, handle, length, data) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoExA(DWORD flags, LPCSTR file, DWORD handle,
                                               DWORD length, LPVOID data) {
    using Function = decltype(&GetFileVersionInfoExA);
    const auto function = version_function<Function>("GetFileVersionInfoExA");
    return function ? function(flags, file, handle, length, data) : FALSE;
}

extern "C" BOOL WINAPI GetFileVersionInfoExW(DWORD flags, LPCWSTR file, DWORD handle,
                                               DWORD length, LPVOID data) {
    using Function = decltype(&GetFileVersionInfoExW);
    const auto function = version_function<Function>("GetFileVersionInfoExW");
    return function ? function(flags, file, handle, length, data) : FALSE;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeA(LPCSTR file, LPDWORD handle) {
    using Function = decltype(&GetFileVersionInfoSizeA);
    const auto function = version_function<Function>("GetFileVersionInfoSizeA");
    return function ? function(file, handle) : 0;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeW(LPCWSTR file, LPDWORD handle) {
    using Function = decltype(&GetFileVersionInfoSizeW);
    const auto function = version_function<Function>("GetFileVersionInfoSizeW");
    return function ? function(file, handle) : 0;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeExA(DWORD flags, LPCSTR file,
                                                    LPDWORD handle) {
    using Function = decltype(&GetFileVersionInfoSizeExA);
    const auto function = version_function<Function>("GetFileVersionInfoSizeExA");
    return function ? function(flags, file, handle) : 0;
}

extern "C" DWORD WINAPI GetFileVersionInfoSizeExW(DWORD flags, LPCWSTR file,
                                                    LPDWORD handle) {
    using Function = decltype(&GetFileVersionInfoSizeExW);
    const auto function = version_function<Function>("GetFileVersionInfoSizeExW");
    return function ? function(flags, file, handle) : 0;
}

extern "C" BOOL WINAPI GetFileVersionInfoByHandle(DWORD flags, HANDLE file,
                                                    LPVOID *data, PDWORD length) {
    using Function = BOOL(WINAPI *)(DWORD, HANDLE, LPVOID *, PDWORD);
    const auto function = version_function<Function>("GetFileVersionInfoByHandle");
    return function ? function(flags, file, data, length) : FALSE;
}

#define FORWARD_DWORD_A(name, signature, call)                      \
    extern "C" DWORD WINAPI name signature {                      \
        using Function = decltype(&name);                          \
        const auto function = version_function<Function>(#name);   \
        return function ? function call : 0;                       \
    }

#define FORWARD_DWORD_W(name, signature, call) FORWARD_DWORD_A(name, signature, call)

FORWARD_DWORD_A(VerFindFileA,
                (DWORD flags, LPSTR file, LPSTR windows, LPSTR app, LPSTR current,
                 PUINT current_length, LPSTR destination, PUINT destination_length),
                (flags, file, windows, app, current, current_length, destination,
                 destination_length))
FORWARD_DWORD_W(VerFindFileW,
                (DWORD flags, LPWSTR file, LPWSTR windows, LPWSTR app,
                 LPWSTR current, PUINT current_length, LPWSTR destination,
                 PUINT destination_length),
                (flags, file, windows, app, current, current_length, destination,
                 destination_length))
FORWARD_DWORD_A(VerInstallFileA,
                (DWORD flags, LPSTR source_file, LPSTR destination_file,
                 LPSTR source_directory, LPSTR destination_directory,
                 LPSTR current_directory, LPSTR temporary_file,
                 PUINT temporary_length),
                (flags, source_file, destination_file, source_directory,
                 destination_directory, current_directory, temporary_file,
                 temporary_length))
FORWARD_DWORD_W(VerInstallFileW,
                (DWORD flags, LPWSTR source_file, LPWSTR destination_file,
                 LPWSTR source_directory, LPWSTR destination_directory,
                 LPWSTR current_directory, LPWSTR temporary_file,
                 PUINT temporary_length),
                (flags, source_file, destination_file, source_directory,
                 destination_directory, current_directory, temporary_file,
                 temporary_length))
FORWARD_DWORD_A(VerLanguageNameA, (DWORD language, LPSTR buffer, DWORD size),
                (language, buffer, size))
FORWARD_DWORD_W(VerLanguageNameW, (DWORD language, LPWSTR buffer, DWORD size),
                (language, buffer, size))

extern "C" BOOL WINAPI VerQueryValueA(LPCVOID block, LPCSTR sub_block,
                                       LPVOID *buffer, PUINT length) {
    using Function = decltype(&VerQueryValueA);
    const auto function = version_function<Function>("VerQueryValueA");
    return function ? function(block, sub_block, buffer, length) : FALSE;
}

extern "C" BOOL WINAPI VerQueryValueW(LPCVOID block, LPCWSTR sub_block,
                                       LPVOID *buffer, PUINT length) {
    using Function = decltype(&VerQueryValueW);
    const auto function = version_function<Function>("VerQueryValueW");
    return function ? function(block, sub_block, buffer, length) : FALSE;
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
        const HANDLE thread = CreateThread(nullptr, 0, install_hooks, nullptr, 0, nullptr);
        if (thread != nullptr) {
            CloseHandle(thread);
        }
    }
    return TRUE;
}
