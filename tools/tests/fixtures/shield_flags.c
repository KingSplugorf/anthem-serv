#define _GNU_SOURCE
#include <sys/mman.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include "patch.h"
static void *region(uintptr_t address) {
    void *p=mmap((void*)(address&~4095UL),4096,PROT_READ|PROT_WRITE|PROT_EXEC,
                 MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    return p==MAP_FAILED?0:p;
}
__attribute__((naked)) static unsigned run(void *data,uint64_t marker) {
    __asm__("push %rbp; sub $0x368,%rsp; mov %rdi,%rbp; mov %rsi,0x358(%rsp);"
            "movabs $0x142b2ce00,%rax; jmp *%rax");
}
int main(void) {
    if(!region(0x142b2ce00)||!region(0x140c8709e))return 77;
    memcpy((void*)0x142b2ce00,patch,sizeof(patch));
    // Resume test adapter: read stored flags and unwind its synthetic frame.
    unsigned char resume[]={0x0f,0xb6,0x85,0xda,0,0,0,0x48,0x81,0xc4,0x68,3,0,0,0x5d,0xc3};
    memcpy((void*)0x140c8709e,resume,sizeof(resume));
    unsigned char data[512]={0};
    uint64_t bridge;memcpy(&bridge,patch+sizeof(patch)-16,8);
    for(unsigned input=0;input<256;input++) {
        data[0xda]=input;
        if(run(data,bridge)!=((input&0xee)|0x20))return 1;
        data[0xda]=input;
        if(run(data,bridge+1)!=(input|0x31))return 2;
    }
    puts("512 native bridge/native flag cases passed");return 0;
}
