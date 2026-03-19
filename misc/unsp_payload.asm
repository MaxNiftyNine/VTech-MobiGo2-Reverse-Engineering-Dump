.CODE

payload: .proc
    push R1 to [SP];
    R1 = SR;
    push R1 to [SP];
    setb [0x2000], 9;
    pop R1 from [SP];
    SR = R1;
    pop R1 from [SP];
    ja do_continue;
    .dw 0xFE8C, 0xD4E3;
do_continue:
    .dw 0xFE80, 0x5383;
    .endp

.END
