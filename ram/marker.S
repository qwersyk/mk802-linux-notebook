/* SPDX-License-Identifier: GPL-2.0-or-later */
.syntax unified
.cpu cortex-a9
.arm
.section .text.entry,"ax",%progbits
.global _start
.type _start,%function
_start:
    push {r0-r3,r12,lr}
    mrs r12,cpsr
    push {r12}
    ldr r0,=0x61f01000
    mov r1,#1
    str r1,[r0,#4]
    adr r2,_start
    str r2,[r0,#8]
    str r12,[r0,#12]
    ldr r3,[r0,#16]
    add r3,r3,#1
    str r3,[r0,#16]
    dmb sy
    ldr r1,=0x4b4f4b4d /* memory bytes MKOK: write commit word last */
    str r1,[r0]
    dsb sy
    pop {r12}
    msr CPSR_f,r12
    pop {r0-r3,r12,pc}
.size _start,.-_start
.ltorg
.section .note.GNU-stack,"",%progbits
