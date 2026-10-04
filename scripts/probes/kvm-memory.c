// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

// Exercise real guest writes to shared file-backed RAM before and after a
// flush, then compare the backing and an independent copy after VM teardown.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/kvm.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

static void fail(const char *operation) {
    perror(operation);
    exit(1);
}

static void flush(int fd, void *ram, size_t length) {
    if (msync(ram, length, MS_SYNC) || fsync(fd)) fail("flush RAM");
}

static unsigned byte_at(int fd) {
    unsigned char value;
    if (pread(fd, &value, 1, 0x8000) != 1) fail("read RAM byte");
    return value;
}

static void run_until_io(int vcpu, struct kvm_run *run) {
    int result;
    do { result = ioctl(vcpu, KVM_RUN, 0); } while (result < 0 && errno == EINTR);
    if (result < 0) fail("KVM_RUN");
    if (run->exit_reason != KVM_EXIT_IO || run->io.direction != KVM_EXIT_IO_OUT ||
        run->io.port != 0xf4 || run->io.size != 1 || run->io.count != 1) {
        fprintf(stderr, "unexpected KVM exit %u\n", run->exit_reason);
        exit(1);
    }
}

int main(void) {
    const size_t length = 2 * 1024 * 1024;
    unsigned changed = 0;
    for (unsigned trial = 0; trial < 20; ++trial) {
        char backing_name[] = "/tmp/nvx-kvm-memory-XXXXXX";
        char copy_name[] = "/tmp/nvx-kvm-copy-XXXXXX";
        int backing = mkstemp(backing_name), copy = mkstemp(copy_name);
        if (backing < 0 || copy < 0) fail("create private RAM files");
        unlink(backing_name);
        unlink(copy_name);
        if (ftruncate(backing, (off_t)length)) fail("size RAM");
        unsigned char *ram = mmap(NULL, length, PROT_READ | PROT_WRITE,
                                  MAP_SHARED, backing, 0);
        if (ram == MAP_FAILED) fail("map RAM");
        // Real-mode guest: write 0x11, exit; write 0x22 to the same page, exit.
        const unsigned char code[] = {
            0xb0, 0x11, 0xa2, 0x00, 0x80, 0xba, 0xf4, 0x00, 0xee,
            0xb0, 0x22, 0xa2, 0x00, 0x80, 0xee, 0xf4
        };
        memcpy(ram + 0x1000, code, sizeof(code));
        flush(backing, ram, length);
        int kvm = open("/dev/kvm", O_RDWR | O_CLOEXEC);
        if (kvm < 0 || ioctl(kvm, KVM_GET_API_VERSION, 0) != 12) fail("KVM API");
        int vm = ioctl(kvm, KVM_CREATE_VM, 0);
        if (vm < 0) fail("create VM");
        struct kvm_userspace_memory_region slot = {
            .slot = 0, .guest_phys_addr = 0, .memory_size = length,
            .userspace_addr = (uintptr_t)ram
        };
        if (ioctl(vm, KVM_SET_USER_MEMORY_REGION, &slot)) fail("register RAM");
        int vcpu = ioctl(vm, KVM_CREATE_VCPU, 0);
        if (vcpu < 0) fail("create vCPU");
        int run_size = ioctl(kvm, KVM_GET_VCPU_MMAP_SIZE, 0);
        if (run_size < (int)sizeof(struct kvm_run)) fail("vCPU mapping size");
        struct kvm_run *run = mmap(NULL, (size_t)run_size, PROT_READ | PROT_WRITE,
                                   MAP_SHARED, vcpu, 0);
        if (run == MAP_FAILED) fail("map vCPU");
        struct kvm_sregs sregs;
        if (ioctl(vcpu, KVM_GET_SREGS, &sregs)) fail("get segment registers");
        sregs.cs.base = 0;
        sregs.cs.selector = 0;
        sregs.cs.db = 0;
        sregs.cs.l = 0;
        sregs.ds.base = 0;
        sregs.ds.selector = 0;
        if (ioctl(vcpu, KVM_SET_SREGS, &sregs)) fail("set segment registers");
        struct kvm_regs regs = {.rip = 0x1000, .rflags = 2};
        if (ioctl(vcpu, KVM_SET_REGS, &regs)) fail("set registers");
        run_until_io(vcpu, run);
        if (byte_at(backing) != 0x11) fail("first guest write");
        flush(backing, ram, length);
        run_until_io(vcpu, run);
        if (byte_at(backing) != 0x22) fail("second guest write");
        flush(backing, ram, length);
        unsigned before = byte_at(backing);
        void *buffer = malloc(length);
        if (!buffer) fail("allocate copy buffer");
        if (pread(backing, buffer, length, 0) != (ssize_t)length ||
            pwrite(copy, buffer, length, 0) != (ssize_t)length || fsync(copy)) {
            fail("persist independent RAM copy");
        }
        free(buffer);
        if (munmap(run, (size_t)run_size)) fail("unmap vCPU");
        close(vcpu);
        if (trial % 2 == 0 && munmap(ram, length)) fail("unmap RAM before VM");
        close(vm);
        if (trial % 2 != 0 && munmap(ram, length)) fail("unmap RAM after VM");
        close(kvm);
        if (fsync(backing)) fail("flush after teardown");
        int advice = posix_fadvise(backing, 0, (off_t)length, POSIX_FADV_DONTNEED);
        if (advice) { errno = advice; fail("evict clean RAM cache"); }
        advice = posix_fadvise(copy, 0, (off_t)length, POSIX_FADV_DONTNEED);
        if (advice) { errno = advice; fail("evict independent cache"); }
        unsigned after = byte_at(backing), independent = byte_at(copy);
        if (independent != 0x22) fail("independent copy lost guest write");
        if (after != before) ++changed;
        printf("trial=%u unmap_first=%u before=%02x after=%02x copy=%02x\n",
               trial + 1, trial % 2 == 0, before, after, independent);
        close(backing);
        close(copy);
    }
    printf("KVM file RAM changed after teardown: %u/20\n", changed);
    return 0;
}
