.intel_syntax noprefix
.code64
start:
    push rbx
    push rsi
    push rdi
    push r12
    sub rsp, 0x58
    mov rbx, rcx
    mov rsi, rdx
    movabs rax, 0x14879d8c0
    call rax
    mov qword ptr [rsp + 0x48], rax
    lea r12, [rbx - 0x3e0]
    movabs rax, 0x143a5bf40
    cmp qword ptr [r12], rax
    jne done
    movss xmm0, dword ptr [rsi + 0x528]
    movss dword ptr [rsp + 0x20], xmm0
    lea rax, [r12 + 0x2f8]
    mov qword ptr [rsp + 0x28], rax
    mov qword ptr [rsp + 0x30], rsi
    mov qword ptr [rsp + 0x38], 0
    mov qword ptr [rsp + 0x40], 0
    mov ebx, 0x2b8
loop:
    mov rdi, qword ptr [r12 + rbx]
    call owned
    test al, al
    je next
    mov rax, qword ptr [rdi]
    mov rcx, rdi
    call qword ptr [rax + 0x20]
    test al, al
    je next
    mov rax, qword ptr [rdi]
    lea rdx, [rsp + 0x20]
    mov rcx, rdi
    call qword ptr [rax + 0x30]
next:
    add ebx, 8
    cmp ebx, 0x2d8
    jb loop
done:
    mov rax, qword ptr [rsp + 0x48]
    add rsp, 0x58
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret
owned:
    xor eax, eax
    test rdi, rdi
    je 1f
    test dil, 7
    jne 1f
    cmp rdi, 0x10000
    jb 1f
    movabs rdx, 0x7ff000000000
    cmp rdi, rdx
    jae 1f
    mov rcx, qword ptr [rdi + 0x10]
    sub rcx, r12
    cmp rcx, 0x460
    jae 1f
    mov rcx, qword ptr [rdi]
    movabs rdx, 0x143660000
    cmp rcx, rdx
    jb 1f
    movabs rdx, 0x1442f0000
    cmp rcx, rdx
    jae 1f
    mov al, 1
1:
    ret
