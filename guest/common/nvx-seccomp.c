#define _GNU_SOURCE
#include <errno.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/sched.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc < 3) return 125;
    if (strcmp(argv[1], "nvx-default") == 0) {
#if defined(__aarch64__)
#define NVX_ARCH AUDIT_ARCH_AARCH64
#elif defined(__x86_64__)
#define NVX_ARCH AUDIT_ARCH_X86_64
#else
#error unsupported NVX seccomp architecture
#endif
#define NVX_ALLOW(number) BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, number, 0, 1), BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        struct sock_filter instructions[] = {
            BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, NVX_ARCH, 1, 0),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
            BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
            /* Classic seccomp cannot dereference clone3's struct clone_args.
               ENOSYS makes libc fall back to clone, whose flags we can inspect. */
#ifdef __NR_clone3
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone3, 0, 1),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ENOSYS),
#endif
#ifdef __NR_clone
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone, 0, 4),
            BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
            BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K,
                     CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWPID |
                     CLONE_NEWUTS | CLONE_NEWIPC | CLONE_NEWCGROUP, 0, 1),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
#endif
#ifdef __NR_socket
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socket, 0, 6),
            BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_UNIX, 3, 0),
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET, 2, 0),
            BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET6, 1, 0),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
#endif
#include "seccomp-nvx-default.h"
            BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        };
        struct sock_fprog program = { .len = sizeof(instructions) / sizeof(instructions[0]), .filter = instructions };
        if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) {
            perror("nvx-seccomp: install"); return 125;
        }
    } else if (strcmp(argv[1], "unconfined") != 0) return 125;
    execvp(argv[2], argv + 2);
    perror("nvx-seccomp: exec");
    return 127;
}
