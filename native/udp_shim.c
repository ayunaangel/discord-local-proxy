/*
 * discord-proxy — componente nativo do ajuste de voz.
 *
 * Um arquivo só para as duas plataformas:
 *
 *   Linux    -> libdiscordproxy.so, carregada com LD_PRELOAD apenas no
 *               processo que o launcher abre. Intercepta send/sendto/sendmsg
 *               com dlsym(RTLD_NEXT).
 *   Windows  -> version.dll, colocada ao lado do Discord.exe (carregamento
 *               lateral). Reencaminha os 17 símbolos do version.dll do sistema
 *               e troca os ponteiros de sendto/WSASendTo na tabela de imports
 *               (IAT) dos módulos do processo.
 *
 * O que ele faz: quando o Discord manda o pacote de descoberta de IP do canal
 * de voz (74 bytes, tipo 0x0001), envia antes o conteúdo de um arquivo .bin
 * opcional, depois um byte 0x00, um byte 0x01, espera alguns milissegundos e
 * só então deixa o pacote original seguir.
 *
 * O que ele NÃO faz: não cria túnel, não manda voz pelo proxy, não desliga
 * verificação de TLS e não toca em nada fora do processo do Discord. A parte
 * de TCP (chat, login, updates) é resolvida pelo `--proxy-server` do Electron
 * junto com a ponte local — este arquivo nem olha para conexões TCP.
 */

#if !defined(_WIN32) && !defined(_GNU_SOURCE)
#  define _GNU_SOURCE
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DISCOVERY_PACKET_SIZE 74u
#define MAX_PACKET_BYTES 65507u
#define MAX_CONFIG_BYTES 65536u
#define MAX_TRACKED 512
#define TRACK_TTL_MS 30000u
#define CONFIG_NAME "discord-proxy.ini"
#define LEGACY_CONFIG_NAME "drover.ini"

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  include <windows.h>
#  include <psapi.h>
#  include <wchar.h>
typedef UINT_PTR socket_handle;
typedef int socklen_type;
typedef SOCKET int_or_socket;
#else
#  include <dlfcn.h>
#  include <errno.h>
#  include <fcntl.h>
#  include <pthread.h>
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <sys/socket.h>
#  include <sys/stat.h>
#  include <sys/uio.h>
#  include <time.h>
#  include <unistd.h>
typedef int socket_handle;
typedef socklen_t socklen_type;
typedef int int_or_socket;
#endif

/* ------------------------------------------------------------------ estado */

struct shim_config {
    int voice;
    unsigned int delay_ms;
    char packet[4096]; /* caminho em UTF-8; vazio = sem pacote extra */
};

struct tracked {
    int used;
    socket_handle handle;
    unsigned long long stamp_ms;
    socklen_type address_length;
    unsigned char address[128];
};

static struct shim_config g_config = {0, 50u, {0}};
static struct tracked g_tracked[MAX_TRACKED];
static char g_state_file[4096];

#ifdef _WIN32
static CRITICAL_SECTION g_lock;
#  define LOCK() EnterCriticalSection(&g_lock)
#  define UNLOCK() LeaveCriticalSection(&g_lock)
#else
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t g_setup_once = PTHREAD_ONCE_INIT;
#  define LOCK() pthread_mutex_lock(&g_lock)
#  define UNLOCK() pthread_mutex_unlock(&g_lock)
#endif

static unsigned long long now_ms(void) {
#ifdef _WIN32
    return (unsigned long long)GetTickCount64();
#else
    struct timespec moment;
    if (clock_gettime(CLOCK_MONOTONIC, &moment) != 0) {
        return 0ull;
    }
    return (unsigned long long)moment.tv_sec * 1000ull +
           (unsigned long long)(moment.tv_nsec / 1000000L);
#endif
}

static void sleep_ms(unsigned int milliseconds) {
    if (milliseconds == 0u) {
        return;
    }
#ifdef _WIN32
    Sleep((DWORD)milliseconds);
#else
    struct timespec wanted;
    struct timespec left;
    wanted.tv_sec = (time_t)(milliseconds / 1000u);
    wanted.tv_nsec = (long)(milliseconds % 1000u) * 1000000L;
    while (nanosleep(&wanted, &left) != 0 && errno == EINTR) {
        wanted = left;
    }
#endif
}

/* ------------------------------------------------------- leitura da config */

static char *trim(char *text) {
    char *end;
    while (*text == ' ' || *text == '\t' || *text == '\r' || *text == '\n') {
        text++;
    }
    end = text + strlen(text);
    while (end > text) {
        char previous = end[-1];
        if (previous != ' ' && previous != '\t' && previous != '\r' && previous != '\n') {
            break;
        }
        *--end = '\0';
    }
    return text;
}

static int equals_ci(const char *left, const char *right) {
    while (*left != '\0' && *right != '\0') {
        char a = *left++;
        char b = *right++;
        if (a >= 'A' && a <= 'Z') {
            a = (char)(a - 'A' + 'a');
        }
        if (b >= 'A' && b <= 'Z') {
            b = (char)(b - 'A' + 'a');
        }
        if (a != b) {
            return 0;
        }
    }
    return *left == *right;
}

static int parse_switch(const char *value, int fallback) {
    static const char *off[] = {"0", "off", "false", "no", "nao"};
    static const char *on[] = {"1", "on", "true", "yes", "sim"};
    size_t index;
    if (value == NULL || *value == '\0') {
        return fallback;
    }
    for (index = 0; index < sizeof(off) / sizeof(off[0]); ++index) {
        if (equals_ci(value, off[index])) {
            return 0;
        }
    }
    for (index = 0; index < sizeof(on) / sizeof(on[0]); ++index) {
        if (equals_ci(value, on[index])) {
            return 1;
        }
    }
    return fallback;
}

static unsigned int parse_delay(const char *value, unsigned int fallback) {
    char *end = NULL;
    long parsed;
    if (value == NULL || *value == '\0') {
        return fallback;
    }
    parsed = strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 0 || parsed > 1000) {
        return fallback;
    }
    return (unsigned int)parsed;
}

/* Um parser de INI curto: só precisamos de voice, delay e packet. */
static void apply_ini(const char *text, struct shim_config *config) {
    const char *cursor = text;
    while (*cursor != '\0') {
        char line[4352];
        const char *end = strchr(cursor, '\n');
        size_t raw_length = (end != NULL) ? (size_t)(end - cursor) : strlen(cursor);
        size_t length = (raw_length < sizeof(line)) ? raw_length : sizeof(line) - 1u;
        char *separator;
        char *key;
        char *value;

        memcpy(line, cursor, length);
        line[length] = '\0';
        cursor += (end != NULL) ? raw_length + 1u : raw_length;

        key = trim(line);
        if (*key == '\0' || *key == ';' || *key == '#' || *key == '[') {
            continue;
        }
        separator = strchr(key, '=');
        if (separator == NULL) {
            continue;
        }
        *separator = '\0';
        value = trim(separator + 1);
        key = trim(key);

        if (strcmp(key, "voice") == 0) {
            config->voice = parse_switch(value, config->voice);
        } else if (strcmp(key, "delay") == 0) {
            config->delay_ms = parse_delay(value, config->delay_ms);
        } else if (strcmp(key, "packet") == 0) {
            size_t size = strlen(value);
            if (size < sizeof(config->packet)) {
                memcpy(config->packet, value, size + 1u);
            }
        }
    }
}

static int read_text_file(const char *path, char *buffer, size_t capacity) {
    FILE *handle;
    size_t read_bytes;
#ifdef _WIN32
    wchar_t wide[4096];
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wide, 4096) == 0) {
        return 0;
    }
    handle = _wfopen(wide, L"rb");
#else
    handle = fopen(path, "rb");
#endif
    if (handle == NULL) {
        return 0;
    }
    read_bytes = fread(buffer, 1u, capacity - 1u, handle);
    fclose(handle);
    buffer[read_bytes] = '\0';
    return read_bytes > 0u;
}

static int load_ini_from(const char *path, struct shim_config *config) {
    static char buffer[MAX_CONFIG_BYTES];
    if (path == NULL || *path == '\0' || !read_text_file(path, buffer, sizeof(buffer))) {
        return 0;
    }
    apply_ini(buffer, config);
    return 1;
}

#ifdef _WIN32
static void utf16_to_utf8(const wchar_t *source, char *destination, int capacity) {
    if (WideCharToMultiByte(CP_UTF8, 0, source, -1, destination, capacity, NULL, NULL) == 0) {
        destination[0] = '\0';
    }
}

static int directory_of(HMODULE module, char *destination, int capacity) {
    wchar_t path[MAX_PATH * 2];
    DWORD length = GetModuleFileNameW(module, path, (DWORD)(sizeof(path) / sizeof(path[0])));
    wchar_t *separator;
    if (length == 0u || length >= sizeof(path) / sizeof(path[0])) {
        return 0;
    }
    separator = wcsrchr(path, L'\\');
    if (separator == NULL) {
        return 0;
    }
    separator[1] = L'\0';
    utf16_to_utf8(path, destination, capacity);
    return destination[0] != '\0';
}

static HMODULE g_self_module = NULL;
#endif

/*
 * No Windows, `getenv` devolve a variável convertida para a codepage ANSI —
 * um caminho com acento (bem provável no nome do usuário) chegaria corrompido.
 * Por isso lemos a versão wide e convertemos para UTF-8 nós mesmos.
 */
static const char *read_env(const char *name, char *buffer, size_t capacity) {
#ifdef _WIN32
    wchar_t wide_name[128];
    const wchar_t *value;
    if (MultiByteToWideChar(CP_UTF8, 0, name, -1, wide_name, 128) == 0) {
        return NULL;
    }
    value = _wgetenv(wide_name);
    if (value == NULL || *value == L'\0') {
        return NULL;
    }
    if (WideCharToMultiByte(CP_UTF8, 0, value, -1, buffer, (int)capacity, NULL, NULL) == 0) {
        return NULL;
    }
    return buffer;
#else
    const char *value = getenv(name);
    (void)buffer;
    (void)capacity;
    return (value != NULL && *value != '\0') ? value : NULL;
#endif
}

static void load_config(void) {
    struct shim_config config = {0, 50u, {0}};
    char env_buffer[4096];
    const char *from_env;
    char candidate[4352];

#ifdef _WIN32
    char directory[4096];
    int loaded = 0;
    from_env = read_env("DISCORD_PROXY_INI", env_buffer, sizeof(env_buffer));
    if (from_env != NULL) {
        loaded = load_ini_from(from_env, &config);
    }
    if (!loaded && directory_of(g_self_module, directory, (int)sizeof(directory))) {
        snprintf(candidate, sizeof(candidate), "%s%s", directory, CONFIG_NAME);
        loaded = load_ini_from(candidate, &config);
        if (!loaded) {
            snprintf(candidate, sizeof(candidate), "%s%s", directory, LEGACY_CONFIG_NAME);
            loaded = load_ini_from(candidate, &config);
        }
    }
    if (!loaded && directory_of(NULL, directory, (int)sizeof(directory))) {
        snprintf(candidate, sizeof(candidate), "%s%s", directory, CONFIG_NAME);
        (void)load_ini_from(candidate, &config);
    }
#else
    from_env = read_env("DISCORD_PROXY_INI", env_buffer, sizeof(env_buffer));
    if (from_env != NULL) {
        (void)load_ini_from(from_env, &config);
    }
    (void)candidate;
#endif

    /* As variáveis de ambiente vêm do launcher e têm a última palavra. */
    from_env = read_env("DISCORD_PROXY_VOICE", env_buffer, sizeof(env_buffer));
    config.voice = parse_switch(from_env, config.voice);
    from_env = read_env("DISCORD_PROXY_DELAY", env_buffer, sizeof(env_buffer));
    config.delay_ms = parse_delay(from_env, config.delay_ms);
    from_env = read_env("DISCORD_PROXY_PACKET", env_buffer, sizeof(env_buffer));
    if (from_env != NULL && strlen(from_env) < sizeof(config.packet)) {
        memcpy(config.packet, from_env, strlen(from_env) + 1u);
    }

    g_state_file[0] = '\0';
    from_env = read_env("DISCORD_PROXY_STATE", env_buffer, sizeof(env_buffer));
    if (from_env != NULL && strlen(from_env) < sizeof(g_state_file)) {
        memcpy(g_state_file, from_env, strlen(from_env) + 1u);
    }

    g_config = config;
}

/* ---------------------------------------------------- controle de sockets */

static void forget_expired(unsigned long long moment) {
    int index;
    for (index = 0; index < MAX_TRACKED; ++index) {
        if (g_tracked[index].used && moment > g_tracked[index].stamp_ms + TRACK_TTL_MS) {
            g_tracked[index].used = 0;
        }
    }
}

/* Verdadeiro só na primeira vez que este socket fala com este destino. */
static int claim_first_send(socket_handle handle, const void *address, socklen_type length) {
    unsigned long long moment = now_ms();
    int index;
    int free_slot = -1;
    unsigned char key[128];
    socklen_type key_length = 0;

    if (address != NULL && length > 0 && (size_t)length <= sizeof(key)) {
        memcpy(key, address, (size_t)length);
        key_length = length;
    }

    LOCK();
    forget_expired(moment);
    for (index = 0; index < MAX_TRACKED; ++index) {
        if (!g_tracked[index].used) {
            if (free_slot < 0) {
                free_slot = index;
            }
            continue;
        }
        if (g_tracked[index].handle == handle &&
            g_tracked[index].address_length == key_length &&
            (key_length == 0 ||
             memcmp(g_tracked[index].address, key, (size_t)key_length) == 0)) {
            UNLOCK();
            return 0;
        }
    }
    if (free_slot < 0) {
        free_slot = 0; /* tabela cheia: reaproveita a entrada mais antiga */
    }
    g_tracked[free_slot].used = 1;
    g_tracked[free_slot].handle = handle;
    g_tracked[free_slot].stamp_ms = moment;
    g_tracked[free_slot].address_length = key_length;
    if (key_length > 0) {
        memcpy(g_tracked[free_slot].address, key, (size_t)key_length);
    }
    UNLOCK();
    return 1;
}

static void forget_socket(socket_handle handle) {
    int index;
    LOCK();
    for (index = 0; index < MAX_TRACKED; ++index) {
        if (g_tracked[index].used && g_tracked[index].handle == handle) {
            g_tracked[index].used = 0;
        }
    }
    UNLOCK();
}

static int is_discovery_packet(const unsigned char *bytes, size_t length) {
    /* Descoberta de IP do Discord: 74 bytes, tipo 0x0001, tamanho 0x0046. */
    return length == DISCOVERY_PACKET_SIZE && bytes[0] == 0x00 && bytes[1] == 0x01 &&
           bytes[2] == 0x00 && bytes[3] == 0x46;
}

/* ---------------------------------------------- envio do preparo de voz -- */

/* Cada plataforma expõe estas duas: mandar bytes crus sem passar pelo hook. */
static int raw_send(socket_handle handle, const void *data, size_t length,
                    const void *address, socklen_type address_length);
static int socket_is_udp(socket_handle handle);

static void send_packet_file(socket_handle handle, const void *address,
                             socklen_type address_length) {
    unsigned char *payload;
    FILE *file;
    size_t read_bytes;
    const char *path = g_config.packet;

    if (path[0] == '\0') {
        return;
    }
#ifdef _WIN32
    {
        wchar_t wide[4096];
        if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wide, 4096) == 0) {
            return;
        }
        file = _wfopen(wide, L"rb");
    }
#else
    file = fopen(path, "rb");
#endif
    if (file == NULL) {
        return;
    }
    payload = (unsigned char *)malloc(MAX_PACKET_BYTES);
    if (payload == NULL) {
        fclose(file);
        return;
    }
    read_bytes = fread(payload, 1u, MAX_PACKET_BYTES, file);
    fclose(file);
    if (read_bytes > 0u) {
        (void)raw_send(handle, payload, read_bytes, address, address_length);
    }
    free(payload);
}

static void prime(socket_handle handle, const void *address, socklen_type address_length) {
    const unsigned char zero = 0x00;
    const unsigned char one = 0x01;
    send_packet_file(handle, address, address_length);
    (void)raw_send(handle, &zero, 1u, address, address_length);
    (void)raw_send(handle, &one, 1u, address, address_length);
    sleep_ms(g_config.delay_ms);
}

/*
 * Anota para onde a chamada está indo. É o que permite ao launcher dizer em
 * que região o Discord te colocou — sem isso, no Windows não há como saber o
 * destino de um socket UDP. Acontece mesmo com o ajuste de voz desligado.
 */
static void record_endpoint(socket_handle handle, const void *address,
                            socklen_type address_length) {
    struct sockaddr_storage storage;
    const struct sockaddr *target = (const struct sockaddr *)address;
    char text[128];
    char line[192];
    FILE *file;
    unsigned short port = 0;
    const void *raw = NULL;

    if (g_state_file[0] == '\0') {
        return;
    }
    if (target == NULL || address_length <= 0) {
        socklen_type size = (socklen_type)sizeof(storage);
        memset(&storage, 0, sizeof(storage));
        if (getpeername((int_or_socket)handle, (struct sockaddr *)&storage, &size) != 0) {
            return;
        }
        target = (const struct sockaddr *)&storage;
    }

    if (target->sa_family == AF_INET) {
        const struct sockaddr_in *v4 = (const struct sockaddr_in *)target;
        raw = &v4->sin_addr;
        port = ntohs(v4->sin_port);
    } else if (target->sa_family == AF_INET6) {
        const struct sockaddr_in6 *v6 = (const struct sockaddr_in6 *)target;
        raw = &v6->sin6_addr;
        port = ntohs(v6->sin6_port);
    } else {
        return;
    }
    if (inet_ntop(target->sa_family, raw, text, (socklen_type)sizeof(text)) == NULL) {
        return;
    }
    snprintf(line, sizeof(line), "%s:%u\n", text, (unsigned)port);

#ifdef _WIN32
    {
        wchar_t wide[4096];
        if (MultiByteToWideChar(CP_UTF8, 0, g_state_file, -1, wide, 4096) == 0) {
            return;
        }
        file = _wfopen(wide, L"ab");
    }
#else
    file = fopen(g_state_file, "ab");
#endif
    if (file == NULL) {
        return;
    }
    fwrite(line, 1u, strlen(line), file);
    fclose(file);
}

static void maybe_prime(socket_handle handle, const unsigned char *bytes, size_t length,
                        const void *address, socklen_type address_length) {
    if (!is_discovery_packet(bytes, length) || !socket_is_udp(handle)) {
        return;
    }
    if (!claim_first_send(handle, address, address_length)) {
        return;
    }
    record_endpoint(handle, address, address_length);
    if (!g_config.voice) {
        return;
    }
    prime(handle, address, address_length);
}

/* ========================================================================= */
/*                                  LINUX                                    */
/* ========================================================================= */

#ifndef _WIN32

typedef ssize_t (*send_fn)(int, const void *, size_t, int);
typedef ssize_t (*sendto_fn)(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
typedef ssize_t (*sendmsg_fn)(int, const struct msghdr *, int);
typedef int (*close_fn)(int);

static send_fn real_send = NULL;
static sendto_fn real_sendto = NULL;
static sendmsg_fn real_sendmsg = NULL;
static close_fn real_close = NULL;

static void setup(void) {
    *(void **)(&real_send) = dlsym(RTLD_NEXT, "send");
    *(void **)(&real_sendto) = dlsym(RTLD_NEXT, "sendto");
    *(void **)(&real_sendmsg) = dlsym(RTLD_NEXT, "sendmsg");
    *(void **)(&real_close) = dlsym(RTLD_NEXT, "close");
    load_config();
}

static void ensure_setup(void) { pthread_once(&g_setup_once, setup); }

static int socket_is_udp(socket_handle handle) {
    int kind = 0;
    socklen_t length = (socklen_t)sizeof(kind);
    return getsockopt(handle, SOL_SOCKET, SO_TYPE, &kind, &length) == 0 && kind == SOCK_DGRAM;
}

static int raw_send(socket_handle handle, const void *data, size_t length,
                    const void *address, socklen_type address_length) {
    if (address != NULL && address_length > 0 && real_sendto != NULL) {
        return (int)real_sendto(handle, data, length, 0, (const struct sockaddr *)address,
                                address_length);
    }
    if (real_send != NULL) {
        return (int)real_send(handle, data, length, 0);
    }
    return -1;
}

__attribute__((visibility("default")))
ssize_t sendto(int fd, const void *buffer, size_t length, int flags,
               const struct sockaddr *address, socklen_t address_length) {
    ensure_setup();
    if (real_sendto == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (buffer != NULL && length >= 4u) {
        maybe_prime(fd, (const unsigned char *)buffer, length, address, address_length);
    }
    return real_sendto(fd, buffer, length, flags, address, address_length);
}

__attribute__((visibility("default")))
ssize_t send(int fd, const void *buffer, size_t length, int flags) {
    ensure_setup();
    if (real_send == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (buffer != NULL && length >= 4u) {
        maybe_prime(fd, (const unsigned char *)buffer, length, NULL, 0);
    }
    return real_send(fd, buffer, length, flags);
}

__attribute__((visibility("default")))
ssize_t sendmsg(int fd, const struct msghdr *message, int flags) {
    ensure_setup();
    if (real_sendmsg == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (message != NULL && message->msg_iov != NULL && message->msg_iovlen > 0) {
        unsigned char head[4] = {0};
        size_t copied = 0;
        size_t total = 0;
        size_t index;
        for (index = 0; index < (size_t)message->msg_iovlen; ++index) {
            const unsigned char *source = (const unsigned char *)message->msg_iov[index].iov_base;
            size_t available = message->msg_iov[index].iov_len;
            total += available;
            if (source != NULL && copied < sizeof(head)) {
                size_t wanted = sizeof(head) - copied;
                size_t amount = available < wanted ? available : wanted;
                memcpy(head + copied, source, amount);
                copied += amount;
            }
        }
        if (copied == sizeof(head)) {
            maybe_prime(fd, head, total, message->msg_name, (socklen_t)message->msg_namelen);
        }
    }
    return real_sendmsg(fd, message, flags);
}

__attribute__((visibility("default")))
int close(int fd) {
    ensure_setup();
    forget_socket(fd);
    if (real_close == NULL) {
        errno = ENOSYS;
        return -1;
    }
    return real_close(fd);
}

#else

/* ========================================================================= */
/*                                 WINDOWS                                   */
/* ========================================================================= */

typedef int(WSAAPI *sendto_fn)(SOCKET, const char *, int, int, const struct sockaddr *, int);
typedef int(WSAAPI *wsasendto_fn)(SOCKET, LPWSABUF, DWORD, LPDWORD, DWORD,
                                  const struct sockaddr *, int, LPWSAOVERLAPPED,
                                  LPWSAOVERLAPPED_COMPLETION_ROUTINE);
typedef int(WSAAPI *closesocket_fn)(SOCKET);

static sendto_fn real_sendto = NULL;
static wsasendto_fn real_wsasendto = NULL;
static closesocket_fn real_closesocket = NULL;

static int WSAAPI hook_sendto(SOCKET handle, const char *buffer, int length, int flags,
                              const struct sockaddr *address, int address_length);
static int WSAAPI hook_wsasendto(SOCKET handle, LPWSABUF buffers, DWORD count, LPDWORD sent,
                                 DWORD flags, const struct sockaddr *address, int address_length,
                                 LPWSAOVERLAPPED overlapped,
                                 LPWSAOVERLAPPED_COMPLETION_ROUTINE completion);
static int WSAAPI hook_closesocket(SOCKET handle);

static int socket_is_udp(socket_handle handle) {
    int kind = 0;
    int length = (int)sizeof(kind);
    return getsockopt((SOCKET)handle, SOL_SOCKET, SO_TYPE, (char *)&kind, &length) == 0 &&
           kind == SOCK_DGRAM;
}

static int raw_send(socket_handle handle, const void *data, size_t length,
                    const void *address, socklen_type address_length) {
    if (real_sendto == NULL) {
        return -1;
    }
    return real_sendto((SOCKET)handle, (const char *)data, (int)length, 0,
                       (const struct sockaddr *)address, (int)address_length);
}

/* ----------------------------------------------------- troca na IAT ----- */

/*
 * Em vez de reescrever o início das funções do Winsock (o que exige uma
 * biblioteca de detours e um desmontador inteiro), trocamos o ponteiro que o
 * módulo usa para chamar cada função. É pouco código, não mexe em nenhuma
 * instrução e desfaz sozinho quando o processo termina.
 */
static int patch_one_module(HMODULE module, const char *function, void *replacement,
                            void *original) {
    unsigned char *base = (unsigned char *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_IMPORT_DESCRIPTOR *import;
    DWORD import_rva;
    int patched = 0;

    if (module == NULL || IsBadReadPtr(base, sizeof(IMAGE_DOS_HEADER)) ||
        dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return 0;
    }
    nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (IsBadReadPtr(nt, sizeof(IMAGE_NT_HEADERS)) || nt->Signature != IMAGE_NT_SIGNATURE) {
        return 0;
    }
    import_rva = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress;
    if (import_rva == 0u) {
        return 0;
    }

    for (import = (IMAGE_IMPORT_DESCRIPTOR *)(base + import_rva); import->Name != 0u; ++import) {
        IMAGE_THUNK_DATA *names;
        IMAGE_THUNK_DATA *addresses;
        if (import->OriginalFirstThunk == 0u || import->FirstThunk == 0u) {
            continue;
        }
        names = (IMAGE_THUNK_DATA *)(base + import->OriginalFirstThunk);
        addresses = (IMAGE_THUNK_DATA *)(base + import->FirstThunk);
        for (; names->u1.AddressOfData != 0u; ++names, ++addresses) {
            IMAGE_IMPORT_BY_NAME *entry;
            DWORD protection = 0;
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) {
                continue;
            }
            entry = (IMAGE_IMPORT_BY_NAME *)(base + names->u1.AddressOfData);
            if (strcmp((const char *)entry->Name, function) != 0) {
                continue;
            }
            if ((void *)addresses->u1.Function != original) {
                continue; /* já trocado, ou aponta para outro lugar */
            }
            if (!VirtualProtect(&addresses->u1.Function, sizeof(void *), PAGE_READWRITE,
                                &protection)) {
                continue;
            }
            addresses->u1.Function = (ULONG_PTR)replacement;
            VirtualProtect(&addresses->u1.Function, sizeof(void *), protection, &protection);
            patched = 1;
        }
    }
    return patched;
}

static void patch_all_modules(void) {
    HMODULE winsock = GetModuleHandleW(L"ws2_32.dll");
    HMODULE modules[512];
    DWORD needed = 0;
    DWORD index;
    DWORD count;

    if (winsock == NULL) {
        return;
    }
    if (real_sendto == NULL) {
        real_sendto = (sendto_fn)(void *)GetProcAddress(winsock, "sendto");
        real_wsasendto = (wsasendto_fn)(void *)GetProcAddress(winsock, "WSASendTo");
        real_closesocket = (closesocket_fn)(void *)GetProcAddress(winsock, "closesocket");
    }
    if (!EnumProcessModules(GetCurrentProcess(), modules, (DWORD)sizeof(modules), &needed)) {
        return;
    }
    count = needed / (DWORD)sizeof(HMODULE);
    if (count > (DWORD)(sizeof(modules) / sizeof(modules[0]))) {
        count = (DWORD)(sizeof(modules) / sizeof(modules[0]));
    }
    for (index = 0; index < count; ++index) {
        if (modules[index] == winsock || modules[index] == g_self_module) {
            continue;
        }
        if (real_sendto != NULL) {
            patch_one_module(modules[index], "sendto", (void *)hook_sendto, (void *)real_sendto);
        }
        if (real_wsasendto != NULL) {
            patch_one_module(modules[index], "WSASendTo", (void *)hook_wsasendto,
                             (void *)real_wsasendto);
        }
        if (real_closesocket != NULL) {
            patch_one_module(modules[index], "closesocket", (void *)hook_closesocket,
                             (void *)real_closesocket);
        }
    }
}

/* O Chromium carrega DLLs enquanto roda; reaplicamos de tempos em tempos. */
static DWORD WINAPI watcher(LPVOID unused) {
    int rounds;
    (void)unused;
    load_config();
    for (rounds = 0;; ++rounds) {
        patch_all_modules();
        sleep_ms(rounds < 30 ? 500u : 3000u);
    }
}

static int WSAAPI hook_sendto(SOCKET handle, const char *buffer, int length, int flags,
                              const struct sockaddr *address, int address_length) {
    if (buffer != NULL && length >= 4) {
        maybe_prime((socket_handle)handle, (const unsigned char *)buffer, (size_t)length, address,
                    address_length);
    }
    return real_sendto(handle, buffer, length, flags, address, address_length);
}

static int WSAAPI hook_wsasendto(SOCKET handle, LPWSABUF buffers, DWORD count, LPDWORD sent,
                                 DWORD flags, const struct sockaddr *address, int address_length,
                                 LPWSAOVERLAPPED overlapped,
                                 LPWSAOVERLAPPED_COMPLETION_ROUTINE completion) {
    if (buffers != NULL && count > 0u && buffers[0].buf != NULL && buffers[0].len >= 4u) {
        size_t total = 0;
        DWORD index;
        for (index = 0; index < count; ++index) {
            total += buffers[index].len;
        }
        maybe_prime((socket_handle)handle, (const unsigned char *)buffers[0].buf, total, address,
                    address_length);
    }
    return real_wsasendto(handle, buffers, count, sent, flags, address, address_length, overlapped,
                          completion);
}

static int WSAAPI hook_closesocket(SOCKET handle) {
    forget_socket((socket_handle)handle);
    return real_closesocket(handle);
}

/* ------------------------------------ reencaminhamento do version.dll --- */

/*
 * O Discord carrega o version.dll do próprio diretório antes do que está em
 * System32. Precisamos responder por todos os símbolos dele e repassar cada
 * chamada para o original. Em x64 todos esses símbolos cabem na convenção de
 * quatro registradores + pilha, então um único formato de repasse serve para
 * todos — passar argumentos a mais é inofensivo, quem limpa a pilha é quem
 * chama.
 */
typedef UINT_PTR(WINAPI *generic_fn)(UINT_PTR, UINT_PTR, UINT_PTR, UINT_PTR, UINT_PTR, UINT_PTR);

static HMODULE g_system_version = NULL;
static CRITICAL_SECTION g_version_lock;

static generic_fn system_version_function(const char *name) {
    generic_fn resolved = NULL;
    EnterCriticalSection(&g_version_lock);
    if (g_system_version == NULL) {
        wchar_t path[MAX_PATH];
        UINT length = GetSystemDirectoryW(path, MAX_PATH);
        if (length > 0u && length + 13u < MAX_PATH) {
            wcscat(path, L"\\version.dll");
            g_system_version = LoadLibraryExW(path, NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (g_system_version == NULL) {
                g_system_version = LoadLibraryW(path);
            }
        }
    }
    if (g_system_version != NULL) {
        resolved = (generic_fn)(void *)GetProcAddress(g_system_version, name);
    }
    LeaveCriticalSection(&g_version_lock);
    return resolved;
}

/* Os nomes reais saem pelo udp_shim.def; aqui usamos o prefixo fwd_ para não
 * colidir com os protótipos que o winver.h já declara. */
#define FORWARD(name)                                                                  \
    UINT_PTR WINAPI fwd_##name(UINT_PTR a, UINT_PTR b, UINT_PTR c, UINT_PTR d,          \
                               UINT_PTR e, UINT_PTR f) {                                \
        static generic_fn original = NULL;                                             \
        if (original == NULL) {                                                        \
            original = system_version_function(#name);                                 \
            if (original == NULL) {                                                    \
                SetLastError(ERROR_PROC_NOT_FOUND);                                    \
                return 0;                                                              \
            }                                                                          \
        }                                                                              \
        return original(a, b, c, d, e, f);                                             \
    }

FORWARD(GetFileVersionInfoA)
FORWARD(GetFileVersionInfoByHandle)
FORWARD(GetFileVersionInfoExA)
FORWARD(GetFileVersionInfoExW)
FORWARD(GetFileVersionInfoSizeA)
FORWARD(GetFileVersionInfoSizeExA)
FORWARD(GetFileVersionInfoSizeExW)
FORWARD(GetFileVersionInfoSizeW)
FORWARD(GetFileVersionInfoW)
FORWARD(VerFindFileA)
FORWARD(VerFindFileW)
FORWARD(VerInstallFileA)
FORWARD(VerInstallFileW)
FORWARD(VerLanguageNameA)
FORWARD(VerLanguageNameW)
FORWARD(VerQueryValueA)
FORWARD(VerQueryValueW)

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_self_module = (HMODULE)instance;
        InitializeCriticalSection(&g_lock);
        InitializeCriticalSection(&g_version_lock);
        DisableThreadLibraryCalls(instance);
        /* O trabalho de verdade fica fora do loader lock. */
        CreateThread(NULL, 0, watcher, NULL, 0, NULL);
    }
    return TRUE;
}

#endif /* _WIN32 */
