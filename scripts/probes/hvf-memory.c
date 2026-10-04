#include <Hypervisor/Hypervisor.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
static void regions(void *p, size_t n) {
  mach_vm_address_t a = (uintptr_t)p, end = a + n;
  while (a < end) {
    mach_vm_address_t base = a;
    mach_vm_size_t size = 0;
    vm_region_basic_info_data_64_t info = {0};
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = 0;
    kern_return_t r =
        mach_vm_region(mach_task_self(), &base, &size, VM_REGION_BASIC_INFO_64,
                       (vm_region_info_t)&info, &count, &object);
    if (object)
      mach_port_deallocate(mach_task_self(), object);
    if (r) {
      printf("region query %x\n", r);
      break;
    }
    printf("region offset=%llu size=%llu prot=%d\n",
           (unsigned long long)(base - (uintptr_t)p), (unsigned long long)size,
           info.protection);
    a = base + size;
  }
}
int main(void) {
  const size_t n = 32 * 1024 * 1024, half = n / 2;
  void *p = mmap(NULL, n, PROT_NONE, MAP_ANON | MAP_PRIVATE, -1, 0);
  if (p == MAP_FAILED)
    return 2;
  if (mmap(p, half, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE | MAP_FIXED,
           -1, 0) == MAP_FAILED)
    return 3;
  FILE *f = tmpfile();
  if (!f || ftruncate(fileno(f), (off_t)half))
    return 4;
  if (mmap((char *)p + half, half, PROT_READ | PROT_WRITE,
           MAP_SHARED | MAP_FIXED, fileno(f), 0) == MAP_FAILED)
    return 5;
  regions(p, n);
  hv_return_t r = hv_vm_create(0);
  printf("create %x\n", r);
  if (r)
    return 6;
  r = hv_vm_map(p, 0, n, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC);
  printf("whole span %x\n", r);
  if (!r)
    hv_vm_unmap(0, n);
  hv_return_t a =
      hv_vm_map(p, 0, half, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC);
  printf("first region %x\n", a);
  hv_return_t b = hv_vm_map((char *)p + half, half, half,
                            HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC);
  printf("second region %x\n", b);
  if (!a)
    hv_vm_unmap(0, half);
  if (!b)
    hv_vm_unmap(half, half);

  char name[64];
  snprintf(name, sizeof(name), "/nvx-hvf-probe-%ld", (long)getpid());
  int fd = shm_open(name, O_RDWR | O_CREAT | O_EXCL, 0600);
  if (fd < 0)
    return 7;
  shm_unlink(name);
  if (ftruncate(fd, (off_t)n))
    return 8;
  void *shared = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  if (shared == MAP_FAILED)
    return 9;
  regions(shared, n);
  hv_return_t shared_status = hv_vm_map(
      shared, 0, n, HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC);
  printf("POSIX shared memory %x\n", shared_status);
  if (!shared_status)
    hv_vm_unmap(0, n);
  hv_vm_destroy();
  munmap(shared, n);
  close(fd);
  munmap(p, n);
  fclose(f);
  return a || b || shared_status;
}
