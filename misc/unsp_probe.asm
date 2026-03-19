.CODE

start: .proc
    nop;
    retf;
    reti;
    int off;
    int irq;
    int fiq;
    int fiq, irq;
    push R1 to [SP];
    push R1, R2 to [SP];
    pop R1 from [SP];
    pop R1, R2 from [SP];
    R1 = 0x1234;
    R2 = 0x5678;
    BP = 0x1111;
    SP = 0x2222;
    SR = 0x3333;
    R1 = BP;
    R1 = SP;
    R1 = SR;
    BP = R1;
    SP = R1;
    SR = R1;
    [0x1234] = R1;
    R1 = [0x1234];
    call target;
    goto target;
    jmp target;
    je target;
    jne target;
    jz target;
    jnz target;
    jcc target;
    jcs target;
    jmi target;
    jpl target;
    jbe target;
    ja target;
    jle target;
    jg target;
    jvc target;
    jvs target;
    [BP+0x03] = R1;
    [BP+0x03] = R4;
    B:[BP+0x03] = R1;
    B:[BP+0x03] = R4;
    R1 = [BP+0x03];
    R4 = [BP+0x03];
    R1 = B:[BP+0x03];
    R4 = B:[BP+0x03];
    R1 = 0x0001;
    R1 += 0x08;
    R4 = 0x0001;
    R4 += 0x08;
    R1 = 0x0000;
    R1 |= 0x0020;
    R4 = 0x0000;
    R4 |= 0x0020;
    setb [0x2000], 9;
    clrb [0x2000], 9;
    invb [0x2000], 9;
    tstb [0x2000], 9;
                           	target:
    retf;
    .endp

.END
