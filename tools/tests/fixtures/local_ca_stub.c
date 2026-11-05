/* Native regression for the generated Microsoft x64 call stub, using only
 * synthetic functions/data. Does not load Anthem or exercise Windows APIs. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <fenv.h>

struct result { int32_t code; uint32_t complete; };
static struct result imported;
static unsigned char saved_xmm[6][16];
static const char certificate[] = "fixture-ca";
static int import_calls;

__attribute__((ms_abi, noinline))
static int import_ca(const char *data, int size)
{
    int ok = size == sizeof(certificate)-1 && memcmp(data, certificate, size) == 0;
    ++import_calls;
    fesetround(FE_UPWARD);
    __asm__ volatile(
        "pxor %%xmm0, %%xmm0\npxor %%xmm1, %%xmm1\npxor %%xmm2, %%xmm2\n"
        "pxor %%xmm3, %%xmm3\npxor %%xmm4, %%xmm4\npxor %%xmm5, %%xmm5\n"
        "mov $123, %%rcx\nmov $456, %%rdx\nmov $789, %%r8\nmov $321, %%r9\n"
        "mov $654, %%r10\nmov $987, %%r11\n"
        : : : "rcx", "rdx", "r8", "r9", "r10", "r11",
                  "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5");
    return ok ? 1 : -99;
}

__attribute__((ms_abi, noinline))
static int continuation(void *state, int secure, const char *host, uint32_t ip, int port)
{
    __asm__ volatile("movdqu %%xmm0, %0" : "=m"(saved_xmm[0]));
    __asm__ volatile("movdqu %%xmm1, %0" : "=m"(saved_xmm[1]));
    __asm__ volatile("movdqu %%xmm2, %0" : "=m"(saved_xmm[2]));
    __asm__ volatile("movdqu %%xmm3, %0" : "=m"(saved_xmm[3]));
    __asm__ volatile("movdqu %%xmm4, %0" : "=m"(saved_xmm[4]));
    __asm__ volatile("movdqu %%xmm5, %0" : "=m"(saved_xmm[5]));
    if (state != (void *)0x12345 || secure != 1 || strcmp(host, "spring18.example") ||
        ip != 0x7f000001 || port != 42230 || fegetround() != FE_DOWNWARD) return 10;
    for (int i = 0; i < 6; ++i)
        for (int j = 0; j < 16; ++j)
            if (saved_xmm[i][j] != 0xff) return 11;
    return 37;
}

static int replace_pointer(unsigned char *code, size_t size, uint64_t marker, uintptr_t value)
{
    int found = 0;
    for (size_t i = 0; i+8 <= size; ++i) {
        uint64_t observed;
        memcpy(&observed, code+i, 8);
        if (observed == marker) { memcpy(code+i, &value, 8); ++found; }
    }
    return found == 1;
}

int main(int argc, char **argv)
{
    if (argc != 2) return 1;
    FILE *source = fopen(argv[1], "rb");
    if (!source) return 2;
    size_t page = sysconf(_SC_PAGESIZE);
    unsigned char *code = mmap(NULL, page, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    if (code == MAP_FAILED) return 3;
    size_t size = fread(code, 1, page, source);
    fclose(source);
    if (!replace_pointer(code,size,0x1111222233334444,(uintptr_t)certificate) ||
        !replace_pointer(code,size,0x5555666677778888,(uintptr_t)&imported) ||
        !replace_pointer(code,size,0x9999aaaabbbbcccc,(uintptr_t)&import_ca) ||
        !replace_pointer(code,size,0xddddeeeeffff0000,(uintptr_t)&continuation)) return 4;
    if (mprotect(code,page,PROT_READ|PROT_EXEC)) return 5;
    typedef int (__attribute__((ms_abi)) *connect_fn)(void*,int,const char*,uint32_t,int);
    connect_fn connect = (connect_fn)code;
    fesetround(FE_DOWNWARD);
    __asm__ volatile(
        "pcmpeqd %%xmm0, %%xmm0\npcmpeqd %%xmm1, %%xmm1\npcmpeqd %%xmm2, %%xmm2\n"
        "pcmpeqd %%xmm3, %%xmm3\npcmpeqd %%xmm4, %%xmm4\npcmpeqd %%xmm5, %%xmm5\n"
        : : : "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5");
    int result = connect((void *)0x12345,1,"spring18.example",0x7f000001,42230);
    munmap(code,page);
    if (result != 37 || import_calls != 1 || imported.code != 1 || imported.complete != 1) {
        fprintf(stderr,"continuation=%d calls=%d import=%d complete=%u\n",result,import_calls,imported.code,imported.complete);
        return 6;
    }
    return 0;
}
