/* Fixed RK3066 RAM-only Linux handoff. No storage-controller access.
 * Cache sequence follows ARMv7 + vendor cache-l2x0.c (pinned in references),
 * including PL310 r2p0 indexed operation and r3p0 sync erratum workarounds.
 * GPL-2.0-or-later; inspect actual hardware snapshot before using.
 */
.syntax unified
.cpu cortex-a9
.arm
.section .text.exec,"ax",%progbits
.global ram_exec_hook
.type ram_exec_hook,%function
ram_exec_hook:
    push {r0-r12,lr}
    mrs r4,cpsr
    push {r4,r5}
    ldr r5,=0x61f01000
    mov r1,#1
    str r1,[r5,#36]
    bl _start
preflight_midr:
    mrc p15,0,r0,c0,c0,0
    ldr r1,=0x413fc090
    cmp r0,r1
    bne rejected
    mrc p15,0,r0,c0,c0,5
    ands r0,r0,#255
    bne rejected
    mrc p15,0,r0,c2,c0,2
    cmp r0,#0
    bne rejected
    mrc p15,0,r0,c2,c0,0
    ldr r1,=0x60040000
    cmp r0,r1
    bne rejected
    ldr r0,=0x10180040
    ldr r0,[r0]
    ldr r1,=0x4f54291a
    cmp r0,r1
    bne rejected
    ldr r7,=0x10138000
    ldr r8,[r7]             /* cache ID */
    str r8,[r5,#40]
    ldr r1,=0xff0003c0
    and r0,r8,r1
    ldr r1,=0x410000c0      /* ARM PL310 */
    cmp r0,r1
    bne rejected
    ldr r0,[r7,#0x104]
    ldr r1,=0x76050001
    cmp r0,r1
    bne rejected
    mov r0,#0
    mov r1,#0
    ldr r3,=0x60081c8c      /* native CSWHandler */
    blx r3
    ldr r3,=0x60081c4c      /* native SendCSW */
    blx r3
    /* Bounded delay for the 13-byte DMA CSW to reach the host before disconnect. */
    ldr r0,=10000000
usb_csw_wait:
1:  subs r0,r0,#1
    bne 1b
    cpsid if
    cps #0x13              /* SVC, no return to the abandoned USB IRQ */
    ldr sp,=0x61f03ff0
    mov r1,#3
    str r1,[r5,#36]
    /* L1 data cache: current LoC=1 on the measured Cortex-A9. */
    mov r0,#0
    mcr p15,2,r0,c0,c0,0   /* CSSELR = L1 D/unified */
    isb sy
    mrc p15,1,r0,c0,c0,0
    and r1,r0,#7
    add r1,r1,#4           /* log2(line bytes) */
    ubfx r2,r0,#3,#10      /* ways minus one */
    clz r3,r2              /* way field shift */
    ubfx r4,r0,#13,#15     /* sets minus one */
2:  mov r6,r4
3:  lsl r0,r2,r3
    orr r0,r0,r6,lsl r1
    mcr p15,0,r0,c7,c14,2 /* clean + invalidate by set/way */
    subs r6,r6,#1
    bpl 3b
    subs r2,r2,#1
    bpl 2b
    dsb sy
    mrc p15,0,r0,c1,c0,0
    bic r0,r0,#4
    mcr p15,0,r0,c1,c0,0   /* D-cache off before outer-cache cleanup */
    isb sy
    /* PL310: measured AUX=0x76050001 => 16 ways, 512 KiB. */
    ldr r0,[r7,#0x100]
    tst r0,#1
    beq outer_off
    and r8,r8,#0x3f
    cmp r8,#4              /* PL310 r2p0 erratum 727915 */
    beq indexed_outer
    mov r0,#3
    str r0,[r7,#0xf40]
    movw r0,#0xffff
    str r0,[r7,#0x7fc]
    ldr r1,=1000000
outer_way_poll:
4:  ldr r2,[r7,#0x7fc]
    tst r2,r0
    beq sync_outer
    subs r1,r1,#1
    bne 4b
    b stopped
indexed_outer:
    mov r3,#0
5:  mov r4,#0
6:  lsl r0,r3,#28
    orr r0,r0,r4,lsl #5
    str r0,[r7,#0x7f8]
    add r4,r4,#1
    cmp r4,#1024
    bne 6b
    add r3,r3,#1
    cmp r3,#16
    bne 5b
sync_outer:
    mov r0,#0
    cmp r8,#5              /* PL310 r3p0 erratum 753970 */
    streq r0,[r7,#0x740]
    strne r0,[r7,#0x730]
    ldr r1,=1000000
outer_sync_poll:
7:  ldr r2,[r7,#0x730]
    tst r2,#1
    beq outer_clean
    subs r1,r1,#1
    bne 7b
    b stopped
outer_clean:
    mov r0,#0
    str r0,[r7,#0xf40]
    str r0,[r7,#0x100]
    dsb sy
outer_off:
    mov r1,#5
    str r1,[r5,#36]
    /* A second, tagged CSW proves outer-cache cleanup completed before Linux. */
    mov r0,#0
    mov r1,#0
    ldr r3,=0x60081c8c
    blx r3
    ldr r0,=0x6009a308
    ldr r1,=0x324b4f43       /* wire bytes COK2 */
    str r1,[r0]
    ldr r3,=0x60081c4c
    blx r3
    ldr r0,=10000000
cache_checkpoint_wait:
9:  subs r0,r0,#1
    bne 9b
    bl finish_abandoned_usb_irq
    /* Diagnostic only: retain USB DMA until kernel start_kernel checkpoint. */
    mov r0,#0
    mcr p15,0,r0,c7,c5,0   /* invalidate I-cache */
    mcr p15,0,r0,c7,c5,6   /* invalidate branch predictor */
    dsb sy
    isb sy
    mrc p15,0,r4,c1,c0,0
    bic r4,r4,#0x1800      /* I-cache + branch prediction off */
    bic r4,r4,#5           /* D-cache + MMU off */
    mov r1,#6
    str r1,[r5,#36]
    mov r0,#0
    movw r1,#3066
    ldr r2,=0x60000800
    ldr r3,=0x60408000
    dsb sy
    mcr p15,0,r4,c1,c0,0
    isb sy
    mcr p15,0,r0,c8,c7,0
    dsb sy
    isb sy
    bx r3
/* Loader file147c reads IAR, calls USB handler for IRQ48, then file14bc
 * writes EOIR. The RAM hook leaves that handler and never reaches its EOIR.
 * Complete ONLY the sole known active IRQ48; reject any unexpected nesting.
 * Never read IAR here: that would acknowledge a new pending interrupt. */
.global finish_abandoned_usb_irq
finish_abandoned_usb_irq:
    ldr r6,=0x1013d000
    ldr r7,[r6,#0x304]
    ldr r0,[r6,#0x300]
    ldr r1,[r6,#0x308]
    orr r0,r0,r1
    ldr r1,[r6,#0x30c]
    orrs r0,r0,r1
    bne stopped
    ldr r9,=0x1013c100
    ldr r8,[r9,#0x14]       /* RPR: read-only, unlike IAR */
    cmp r7,#0
    beq irq_verify_idle
    cmp r7,#0x10000         /* IRQ48 == bank1 bit16 */
    bne stopped
    cmp r8,#255
    beq stopped
    mov r0,#48
    str r0,[r9,#0x10]       /* same EOIR as original Loader handler */
    dsb sy
irq_verify_idle:
    ldr r10,[r6,#0x304]
    ldr r0,[r9,#0x14]
    cmp r10,#0
    bne stopped
    cmp r0,#255
    bne stopped
    ldr r1,=0x6001f000      /* reserved tracer state, survives allocator */
    str r7,[r1,#248]
    str r10,[r1,#252]
    orr r0,r0,r8,lsl #16
    str r0,[r1,#24]
    bx lr
rejected:
    mov r1,#0xff
    str r1,[r5,#36]
    mov r0,#1
    mov r1,#0
    ldr r3,=0x60081c8c
    blx r3
    ldr r3,=0x60081c4c
    blx r3
    pop {r4,r5}
    msr CPSR_f,r4
    pop {r0-r12,pc}
stopped:
    mov r1,#0xee
    str r1,[r5,#36]
    dsb sy
8:  wfi
    b 8b                   /* bounded polling ended; no retry/reset/storage */
.size ram_exec_hook,.-ram_exec_hook
.ltorg
.section .state,"aw",%progbits
.global proof_state
proof_state:
    .space 128,0
.section .note.GNU-stack,"",%progbits
