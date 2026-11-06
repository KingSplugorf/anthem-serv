.intel_syntax noprefix
.code64
# Entered instead of the seven-byte load at 0x140c87088.
# The native generic-damage frame is 0x358 below its caller's return address.
start:
    movzx eax, byte ptr [rbp + 0xda]
    push rdx
    mov rdx, qword ptr [rsp + 0x360]
    cmp rdx, qword ptr [rip + bridge_return]
    jne native_flags
    and al, 0xee
    or al, 0x20
    jmp store
native_flags:
    or al, 0x31
store:
    pop rdx
    movss dword ptr [rbp + 0x40], xmm6
    mov byte ptr [rbp + 0xda], al
    jmp qword ptr [rip + resume]
.balign 8
bridge_return:
    .quad 0 # populated from the verified shipped impacts blob, not a guessed offset
resume:
    .quad 0x140c8709e
