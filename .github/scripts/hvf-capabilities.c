#include <Hypervisor/hv.h>
#include <Hypervisor/hv_vmx.h>
#include <inttypes.h>
#include <stdio.h>
#include <sys/sysctl.h>

int main(void) {
    char brand[256] = {0};
    size_t length = sizeof(brand);
    if (sysctlbyname("machdep.cpu.brand_string", brand, &length, NULL, 0) == 0) {
        printf("cpu=%s\n", brand);
    }
    hv_return_t status = hv_vm_create(HV_VM_DEFAULT);
    printf("create_vm=%#x\n", status);
    if (status != HV_SUCCESS) {
        return 1;
    }
    hv_vcpuid_t cpu;
    status = hv_vcpu_create(&cpu, HV_VCPU_DEFAULT);
    printf("create_vcpu=%#x\n", status);
    if (status != HV_SUCCESS) {
        hv_vm_destroy();
        return 1;
    }
    const uint32_t fields[] = {0x4000, 0x4002, 0x401e, 0x4012, 0x400c, 0x2804, 0x2806};
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
        uint64_t required = 0, allowed = 0, value = 0;
        status = hv_vmx_vcpu_get_cap_write_vmcs(cpu, fields[i], &required, &allowed);
        printf("vmcs=%#x cap_status=%#x required=%#" PRIx64 " allowed=%#" PRIx64 "\n",
               fields[i], status, required, allowed);
        status = hv_vmx_vcpu_read_vmcs(cpu, fields[i], &value);
        printf("vmcs=%#x read_status=%#x value=%#" PRIx64 "\n", fields[i], status, value);
    }
    const uint32_t msrs[] = {0x277, 0xc0000080, 0xc0000081, 0xc0000082, 0xc0000100};
    for (size_t i = 0; i < sizeof(msrs) / sizeof(msrs[0]); i++) {
        uint64_t value = 0;
        status = hv_vcpu_enable_native_msr(cpu, msrs[i], true);
        printf("msr=%#x enable_native=%#x\n", msrs[i], status);
        status = hv_vcpu_read_msr(cpu, msrs[i], &value);
        printf("msr=%#x read_status=%#x value=%#" PRIx64 "\n", msrs[i], status, value);
        if (status == HV_SUCCESS) {
            status = hv_vcpu_write_msr(cpu, msrs[i], value);
            printf("msr=%#x write_same_value=%#x\n", msrs[i], status);
        }
        status = hv_vcpu_enable_native_msr(cpu, msrs[i], false);
        printf("msr=%#x disable_native=%#x\n", msrs[i], status);
    }
    status = hv_vmx_vcpu_write_vmcs(cpu, 0x2804, UINT64_C(0x0007040600070406));
    printf("write_guest_pat_vmcs=%#x\n", status);
    status = hv_vmx_vcpu_write_vmcs(cpu, 0x2806, 0);
    printf("write_guest_efer_vmcs=%#x\n", status);
    status = hv_vcpu_destroy(cpu);
    printf("destroy_vcpu=%#x\n", status);
    hv_return_t vm_status = hv_vm_destroy();
    printf("destroy_vm=%#x\n", vm_status);
    return status == HV_SUCCESS && vm_status == HV_SUCCESS ? 0 : 1;
}
