#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/bpf.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
#define INS(code_, dst_, src_, off_, imm_) { .code=code_, .dst_reg=dst_, .src_reg=src_, .off=off_, .imm=imm_ }
/* The context low word uses 1=block, 2=character, not access permission bits. */
_Static_assert(BPF_DEVCG_DEV_BLOCK == 1 && BPF_DEVCG_DEV_CHAR == 2,
               "unexpected cgroup device context encoding");
int main(int argc, char **argv)
{
    if (argc != 2) return 125;
    /* cgroup device context: access_type, major, minor. Permit only the
       private /dev standard character devices and devpts; block all disks. */
    enum { MEMORY_MINOR = 8, TTY_MINOR = 14, ALLOW = 18, DENY = 20 };
#define JUMP(op, reg, value, current, target) INS(BPF_JMP|op|BPF_K, reg, 0, target-current-1, value)
#define GOTO(current, target) INS(BPF_JMP|BPF_JA, 0, 0, target-current-1, 0)
    struct bpf_insn instructions[] = {
        INS(BPF_LDX|BPF_MEM|BPF_W, 2, 1, 0, 0),
        INS(BPF_ALU|BPF_AND|BPF_K, 2, 0, 0, 0xffff),
        JUMP(BPF_JNE, 2, BPF_DEVCG_DEV_CHAR, 2, DENY),
        INS(BPF_LDX|BPF_MEM|BPF_W, 2, 1, 4, 0),
        JUMP(BPF_JEQ, 2, 1, 4, MEMORY_MINOR),
        JUMP(BPF_JEQ, 2, 136, 5, ALLOW),
        JUMP(BPF_JEQ, 2, 5, 6, TTY_MINOR),
        GOTO(7, DENY),
        INS(BPF_LDX|BPF_MEM|BPF_W, 2, 1, 8, 0),
        JUMP(BPF_JEQ, 2, 3, 9, ALLOW),
        JUMP(BPF_JEQ, 2, 5, 10, ALLOW),
        JUMP(BPF_JLT, 2, 7, 11, DENY),
        JUMP(BPF_JGT, 2, 9, 12, DENY),
        GOTO(13, ALLOW),
        INS(BPF_LDX|BPF_MEM|BPF_W, 2, 1, 8, 0),
        JUMP(BPF_JEQ, 2, 0, 15, ALLOW),
        JUMP(BPF_JEQ, 2, 2, 16, ALLOW),
        GOTO(17, DENY),
        INS(BPF_ALU64|BPF_MOV|BPF_K, 0, 0, 0, 1),
        INS(BPF_JMP|BPF_EXIT, 0, 0, 0, 0),
        INS(BPF_ALU64|BPF_MOV|BPF_K, 0, 0, 0, 0),
        INS(BPF_JMP|BPF_EXIT, 0, 0, 0, 0),
    };
    char log[4096] = {0};
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.prog_type = BPF_PROG_TYPE_CGROUP_DEVICE;
    attr.insn_cnt = sizeof(instructions)/sizeof(instructions[0]);
    attr.insns = (uint64_t)(uintptr_t)instructions;
    attr.license = (uint64_t)(uintptr_t)"GPL";
    attr.log_buf = (uint64_t)(uintptr_t)log;
    attr.log_size = sizeof(log);
    attr.log_level = 1;
    int program = syscall(__NR_bpf, BPF_PROG_LOAD, &attr, sizeof(attr));
    if (program < 0) { perror("nvx-device-policy: load"); fprintf(stderr, "%s", log); return 125; }
    int cgroup = open(argv[1], O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    memset(&attr, 0, sizeof(attr));
    attr.target_fd = cgroup;
    attr.attach_bpf_fd = program;
    attr.attach_type = BPF_CGROUP_DEVICE;
    int result = cgroup < 0 ? -1 : syscall(__NR_bpf, BPF_PROG_ATTACH, &attr, sizeof(attr));
    close(program); close(cgroup);
    if (result < 0) { perror("nvx-device-policy: attach"); return 125; }
    puts("NVX-DEVICE-POLICY-OK");
    return 0;
}
