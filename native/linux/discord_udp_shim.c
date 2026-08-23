#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#define EXPORT __attribute__((visibility("default")))
#define DISCOVERY_PACKET_SIZE 74U
#define MAX_VOICE_PACKET_SIZE 65507U
#define MAX_DESTINATION_SIZE ((socklen_t)sizeof(struct sockaddr_storage))

typedef ssize_t (*send_fn)(int, const void *, size_t, int);
typedef ssize_t (*sendto_fn)(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
typedef ssize_t (*sendmsg_fn)(int, const struct msghdr *, int);
typedef int (*close_fn)(int);

static send_fn real_send = NULL;
static sendto_fn real_sendto = NULL;
static sendmsg_fn real_sendmsg = NULL;
static close_fn real_close = NULL;
static pthread_once_t resolve_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t state_lock = PTHREAD_MUTEX_INITIALIZER;
static int voice_enabled = 1;
static unsigned int voice_delay_ms = 50;
static char *voice_packet_file = NULL;

struct primed_peer {
    dev_t device;
    ino_t inode;
    socklen_t destination_length;
    unsigned char destination[sizeof(struct sockaddr_storage)];
    struct primed_peer *next;
};

static struct primed_peer *primed_peers = NULL;

static void *resolve_symbol(const char *name) {
    void *symbol = dlsym(RTLD_NEXT, name);
    return symbol;
}

static int parse_enabled(const char *value, int fallback) {
    if (value == NULL || *value == '\0') {
        return fallback;
    }
    if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 ||
        strcasecmp(value, "no") == 0 || strcasecmp(value, "off") == 0) {
        return 0;
    }
    if (strcmp(value, "1") == 0 || strcasecmp(value, "true") == 0 ||
        strcasecmp(value, "yes") == 0 || strcasecmp(value, "on") == 0) {
        return 1;
    }
    return fallback;
}

static unsigned int parse_delay(const char *value, unsigned int fallback) {
    char *end = NULL;
    long parsed;
    if (value == NULL || *value == '\0') {
        return fallback;
    }
    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed < 0 || parsed > 1000) {
        return fallback;
    }
    return (unsigned int)parsed;
}

static void resolve_functions(void) {
    const char *packet_file;
    *(void **)(&real_send) = resolve_symbol("send");
    *(void **)(&real_sendto) = resolve_symbol("sendto");
    *(void **)(&real_sendmsg) = resolve_symbol("sendmsg");
    *(void **)(&real_close) = resolve_symbol("close");
    voice_enabled = parse_enabled(getenv("DISCORD_LOCAL_PROXY_VOICE_ENABLED"), 1);
    voice_delay_ms = parse_delay(getenv("DISCORD_LOCAL_PROXY_VOICE_DELAY_MS"), 50);
    packet_file = getenv("DISCORD_LOCAL_PROXY_VOICE_PACKET_FILE");
    if (packet_file != NULL && *packet_file != '\0') {
        voice_packet_file = strdup(packet_file);
    }
}

static int is_udp_socket(int fd) {
    int socket_type = 0;
    socklen_t length = (socklen_t)sizeof(socket_type);
    return getsockopt(fd, SOL_SOCKET, SO_TYPE, &socket_type, &length) == 0 &&
           socket_type == SOCK_DGRAM;
}

static int discovery_signature(const unsigned char first_four[4], size_t length) {
    return length == DISCOVERY_PACKET_SIZE && first_four[0] == 0x00 &&
           first_four[1] == 0x01 && first_four[2] == 0x00 && first_four[3] == 0x46;
}

static int destination_for_socket(int fd, const struct sockaddr *provided,
                                  socklen_t provided_length,
                                  struct sockaddr_storage *destination,
                                  socklen_t *destination_length) {
    memset(destination, 0, sizeof(*destination));
    if (provided != NULL && provided_length > 0 && provided_length <= MAX_DESTINATION_SIZE) {
        memcpy(destination, provided, provided_length);
        *destination_length = provided_length;
        return 1;
    }
    *destination_length = MAX_DESTINATION_SIZE;
    if (getpeername(fd, (struct sockaddr *)destination, destination_length) != 0) {
        return 0;
    }
    return *destination_length > 0 && *destination_length <= MAX_DESTINATION_SIZE;
}

static int mark_first_peer(int fd, const struct sockaddr_storage *destination,
                           socklen_t destination_length) {
    struct stat info;
    struct primed_peer *cursor;
    struct primed_peer *entry;
    int first = 0;

    if (fstat(fd, &info) != 0) {
        return 0;
    }
    pthread_mutex_lock(&state_lock);
    for (cursor = primed_peers; cursor != NULL; cursor = cursor->next) {
        if (cursor->device == info.st_dev && cursor->inode == info.st_ino &&
            cursor->destination_length == destination_length &&
            memcmp(cursor->destination, destination, destination_length) == 0) {
            pthread_mutex_unlock(&state_lock);
            return 0;
        }
    }
    entry = (struct primed_peer *)calloc(1, sizeof(*entry));
    if (entry != NULL) {
        entry->device = info.st_dev;
        entry->inode = info.st_ino;
        entry->destination_length = destination_length;
        memcpy(entry->destination, destination, destination_length);
        entry->next = primed_peers;
        primed_peers = entry;
        first = 1;
    }
    pthread_mutex_unlock(&state_lock);
    return first;
}

static void forget_socket(int fd) {
    struct stat info;
    struct primed_peer **link;
    if (fstat(fd, &info) != 0) {
        return;
    }
    pthread_mutex_lock(&state_lock);
    link = &primed_peers;
    while (*link != NULL) {
        struct primed_peer *entry = *link;
        if (entry->device == info.st_dev && entry->inode == info.st_ino) {
            *link = entry->next;
            free(entry);
        } else {
            link = &entry->next;
        }
    }
    pthread_mutex_unlock(&state_lock);
}

static void delay_after_probes(void) {
    struct timespec requested;
    struct timespec remaining;
    if (voice_delay_ms == 0) {
        return;
    }
    requested.tv_sec = (time_t)(voice_delay_ms / 1000U);
    requested.tv_nsec = (long)(voice_delay_ms % 1000U) * 1000000L;
    while (nanosleep(&requested, &remaining) != 0 && errno == EINTR) {
        requested = remaining;
    }
}

static void send_custom_packet(int fd, const struct sockaddr *destination,
                               socklen_t destination_length) {
    int file;
    struct stat info;
    unsigned char *data;
    size_t offset = 0;
    if (voice_packet_file == NULL || *voice_packet_file == '\0') {
        return;
    }
    file = open(voice_packet_file, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (file < 0 || fstat(file, &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_size < 1 || (uintmax_t)info.st_size > MAX_VOICE_PACKET_SIZE) {
        if (file >= 0) {
            (void)close(file);
        }
        return;
    }
    data = (unsigned char *)malloc((size_t)info.st_size);
    if (data == NULL) {
        (void)close(file);
        return;
    }
    while (offset < (size_t)info.st_size) {
        ssize_t amount = read(file, data + offset, (size_t)info.st_size - offset);
        if (amount > 0) {
            offset += (size_t)amount;
        } else if (amount < 0 && errno == EINTR) {
            continue;
        } else {
            break;
        }
    }
    (void)close(file);
    if (offset == (size_t)info.st_size) {
        if (destination != NULL && destination_length > 0 && real_sendto != NULL) {
            (void)real_sendto(fd, data, offset, 0, destination, destination_length);
        } else if (real_send != NULL) {
            (void)real_send(fd, data, offset, 0);
        }
    }
    free(data);
}

static void prime_socket(int fd, int flags, const struct sockaddr *destination,
                         socklen_t destination_length) {
    const unsigned char zero = 0x00;
    const unsigned char one = 0x01;
    (void)flags;
    send_custom_packet(fd, destination, destination_length);
    if (destination != NULL && destination_length > 0) {
        if (real_sendto == NULL) {
            return;
        }
        (void)real_sendto(fd, &zero, 1, 0, destination, destination_length);
        (void)real_sendto(fd, &one, 1, 0, destination, destination_length);
    } else if (real_send != NULL) {
        (void)real_send(fd, &zero, 1, 0);
        (void)real_send(fd, &one, 1, 0);
    } else {
        return;
    }
    delay_after_probes();
}

static void maybe_prime(int fd, const unsigned char first_four[4], size_t length,
                        int flags, const struct sockaddr *provided,
                        socklen_t provided_length) {
    struct sockaddr_storage destination;
    socklen_t destination_length;
    if (!voice_enabled || !discovery_signature(first_four, length) ||
        !is_udp_socket(fd) ||
        !destination_for_socket(fd, provided, provided_length, &destination,
                                &destination_length) ||
        !mark_first_peer(fd, &destination, destination_length)) {
        return;
    }
    prime_socket(fd, flags, provided, provided_length);
}

EXPORT ssize_t sendto(int fd, const void *buffer, size_t length, int flags,
                      const struct sockaddr *destination, socklen_t destination_length) {
    unsigned char signature[4] = {0};
    pthread_once(&resolve_once, resolve_functions);
    if (real_sendto == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (buffer != NULL && length >= sizeof(signature)) {
        memcpy(signature, buffer, sizeof(signature));
        maybe_prime(fd, signature, length, flags, destination, destination_length);
    }
    return real_sendto(fd, buffer, length, flags, destination, destination_length);
}

EXPORT ssize_t send(int fd, const void *buffer, size_t length, int flags) {
    unsigned char signature[4] = {0};
    pthread_once(&resolve_once, resolve_functions);
    if (real_send == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (buffer != NULL && length >= sizeof(signature)) {
        memcpy(signature, buffer, sizeof(signature));
        maybe_prime(fd, signature, length, flags, NULL, 0);
    }
    return real_send(fd, buffer, length, flags);
}

static int first_iov_bytes(const struct iovec *iov, size_t count,
                           unsigned char output[4], size_t *total) {
    size_t copied = 0;
    size_t index;
    *total = 0;
    for (index = 0; index < count; ++index) {
        const unsigned char *source = (const unsigned char *)iov[index].iov_base;
        size_t available = iov[index].iov_len;
        size_t wanted = sizeof(unsigned char) * 4U - copied;
        *total += available;
        if (source != NULL && copied < 4U) {
            size_t amount = available < wanted ? available : wanted;
            memcpy(output + copied, source, amount);
            copied += amount;
        }
    }
    return copied == 4U;
}

EXPORT ssize_t sendmsg(int fd, const struct msghdr *message, int flags) {
    unsigned char signature[4] = {0};
    size_t total = 0;
    pthread_once(&resolve_once, resolve_functions);
    if (real_sendmsg == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (message != NULL && message->msg_iov != NULL &&
        first_iov_bytes(message->msg_iov, message->msg_iovlen, signature, &total)) {
        maybe_prime(fd, signature, total, flags,
                    (const struct sockaddr *)message->msg_name,
                    (socklen_t)message->msg_namelen);
    }
    return real_sendmsg(fd, message, flags);
}

EXPORT int close(int fd) {
    pthread_once(&resolve_once, resolve_functions);
    forget_socket(fd);
    if (real_close == NULL) {
        errno = ENOSYS;
        return -1;
    }
    return real_close(fd);
}
