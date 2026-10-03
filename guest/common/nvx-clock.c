/* Trusted outer-agent clock repair; never installed in the workload /dev. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/fs.h>
#include <linux/random.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#if defined(__aarch64__)
static void generation(void *page, char result[33])
{
    uint64_t words[2] = {
        *(volatile uint64_t *)((char *)page + 16),
        *(volatile uint64_t *)((char *)page + 24)
    };
    const unsigned char *bytes = (const unsigned char *)words;
    for (unsigned int i = 0; i < 16; ++i) sprintf(result + i * 2, "%02x", bytes[i]);
}

static int reseed(void *page)
{
    struct { int entropy_count; int buf_size; unsigned char bytes[32]; } seed = {256, 32, {0}};
    for (unsigned int i = 0; i < 4; ++i) {
        uint64_t word = *(volatile uint64_t *)((char *)page + 32 + i * 8);
        memcpy(seed.bytes + i * 8, &word, 8);
    }
    int fd = open("/dev/random", O_RDWR | O_CLOEXEC);
    if (fd < 0) return -1;
    int result = ioctl(fd, RNDADDENTROPY, &seed);
    if (result == 0) result = ioctl(fd, RNDRESEEDCRNG, 0);
    memset(&seed, 0, sizeof(seed));
    close(fd);
    return result;
}
#endif

int main(int argc, char **argv)
{
#if defined(__aarch64__)
    int fd = open("/dev/mem", O_RDONLY | O_CLOEXEC);
    if (fd < 0) { perror("nvx-clock: /dev/mem"); return 1; }
    void *page = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0xEF001000);
    close(fd);
    if (page == MAP_FAILED) { perror("nvx-clock: mmap"); return 1; }
    char current_generation[33];
    generation(page, current_generation);
    if (argc == 2 && strcmp(argv[1], "--generation") == 0) {
        puts(current_generation); munmap(page, 4096); return 0;
    }
    int resume = argc == 4 && strcmp(argv[1], "--resume") == 0;
    if (resume) {
        if (strlen(argv[2]) != 32) return 1;
        while (strcmp(argv[2], current_generation) == 0) {
            struct timespec pause = {0, 1000000}; nanosleep(&pause, NULL);
            generation(page, current_generation);
        }
        int scratch = open("/run/nvx/scratch", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (scratch < 0 || ioctl(scratch, FITHAW, 0) != 0) return 1;
        close(scratch);
        if (strcmp(argv[3], "repair") != 0) { munmap(page, 4096); return 0; }
        int mid = open("/run/nvx/workload-machine-id", O_WRONLY | O_TRUNC | O_NOFOLLOW | O_CLOEXEC);
        if (mid < 0 || dprintf(mid, "%s\n", current_generation) != 33) return 1;
        close(mid);
        /* Frozen scratch is copied exactly; invalidate clean cached blocks/pages
         * before any new workload can observe its replacement host file. */
        sync();
        int drop = open("/proc/sys/vm/drop_caches", O_WRONLY | O_CLOEXEC);
        if (drop < 0 || write(drop, "3\n", 2) != 2) return 1;
        close(drop);
    }
    uint64_t previous[2] = {
        *(volatile uint64_t *)((char *)page + 16),
        *(volatile uint64_t *)((char *)page + 24)
    };
    int watch = argc == 2 && strcmp(argv[1], "--watch") == 0;
    for (;;) {
    uint64_t seconds = *(volatile uint64_t *)((char *)page + 8);
    if (seconds < 1577836800 || seconds > 4102444800) {
        fprintf(stderr, "nvx-clock: invalid host clock sample\n");
        return 1;
    }
    struct timespec now = { .tv_sec = (time_t)seconds, .tv_nsec = 0 };
    if (reseed(page) != 0) { perror("nvx-clock: reseed"); return 1; }
    if (clock_settime(CLOCK_REALTIME, &now) != 0) {
        perror("nvx-clock: clock_settime"); return 1;
    }
    printf("NVX-CLOCK-SYNC-OK: %llu\n", (unsigned long long)seconds);
    fflush(stdout);
    if (!watch) { munmap(page, 4096); return 0; }
    printf("NVX-CLOCK-WATCH-READY\n");
    fflush(stdout);
    for (;;) {
        struct timespec interval = { .tv_sec = 0, .tv_nsec = 100000000 };
        nanosleep(&interval, NULL);
        uint64_t current[2] = {
            *(volatile uint64_t *)((char *)page + 16),
            *(volatile uint64_t *)((char *)page + 24)
        };
        if (memcmp(previous, current, sizeof(previous)) != 0) {
            memcpy(previous, current, sizeof(previous));
            printf("NVX-CLOCK-RESTORE-DETECTED\n");
            break;
        }
    }
    }
#else
    if ((argc == 2 && strcmp(argv[1], "--generation") == 0) ||
        (argc == 4 && strcmp(argv[1], "--resume") == 0)) {
        int resume = argc == 4;
        char generation[34] = {0};
        for (;;) {
            FILE *sample = popen("/sbin/nvx-port-io read-generation-id 233 234", "r");
            if (!sample || !fgets(generation, sizeof(generation), sample)) return 1;
            if (pclose(sample) || strlen(generation) != 33) return 1;
            generation[32] = 0;
            if (!resume) { puts(generation); return 0; }
            if (strlen(argv[2]) != 32) return 1;
            if (strcmp(generation, argv[2])) break;
            struct timespec delay = {0, 1000000}; nanosleep(&delay, NULL);
        }
        int scratch = open("/run/nvx/scratch", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (scratch < 0 || ioctl(scratch, FITHAW, 0)) return 1;
        close(scratch);
        if (strcmp(argv[3], "repair")) return 0;
        if (system("/sbin/nvx-port-io read-restore-packet 233 234 /run/nvx/warm-restore-packet >/dev/null")) return 1;
        unsigned char entropy[64];
        FILE *packet = fopen("/run/nvx/warm-restore-packet", "rb");
        if (!packet || fseek(packet, -64, SEEK_END) || fread(entropy, 1, 64, packet) != 64) return 1;
        fclose(packet);
        int seed = open("/run/nvx/warm-restore-entropy", O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (seed < 0 || write(seed, entropy, 64) != 64) return 1;
        close(seed); memset(entropy, 0, sizeof(entropy));
        pid_t child = fork();
        if (child < 0) return 1;
        if (!child) { execl("/sbin/nvx-reseed", "nvx-reseed", "/run/nvx/warm-restore-entropy", argv[2], (char *)NULL); _exit(125); }
        int status;
        if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status)) return 1;
        unlink("/run/nvx/warm-restore-entropy"); unlink("/run/nvx/warm-restore-packet");
        int mid = open("/run/nvx/workload-machine-id", O_WRONLY | O_TRUNC | O_NOFOLLOW | O_CLOEXEC);
        if (mid < 0 || dprintf(mid, "%s\n", generation) != 33) return 1;
        close(mid); sync();
        int drop = open("/proc/sys/vm/drop_caches", O_WRONLY | O_CLOEXEC);
        if (drop < 0 || write(drop, "3\n", 2) != 2) return 1;
        close(drop); return 0;
    }
    execl("/sbin/nvx-reseed", "nvx-reseed", "--clock", (char *)NULL);
    perror("nvx-clock: nvx-reseed");
    return 1;
#endif
}
