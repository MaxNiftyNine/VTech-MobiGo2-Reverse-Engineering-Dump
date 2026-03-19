	//  C:\PROGRA~2\GENERA~1\UNSPID~1.1\TOOLCH~2\be::1.1.5

	//-----------------------------------------------------------
	// Compiling C:\Users\Max\Desktop\testtProj2\main.c (C:\Users\Max\AppData\Local\Temp\yAnCXQnzWL\ccB.3)
	//-----------------------------------------------------------

	//-----------------------------------------------------------
	// Options:
	//-----------------------------------------------------------
	//  Target:unSP, ISA:ISA_2.0, Pointer Size:32
	//  -O0	(Optimization level)
	//  -g2	(Debug level)
	//  -m1	(Report warnings)
	//  -mglobal-var-iram (Put global-var with no initval in .iram)
	//  -mpack-string-bigendian (Pack String with Big Endian)
	//-----------------------------------------------------------

.stabs "C:\Users\Max\Desktop\testtProj2",100,0,3,Ltext0
.stabs "C:\Users\Max\Desktop\testtProj2\main.c",100,0,3,Ltext0

.code
Ltext0:
.stabs "int:t1=r1;-32768;32767;",128,0,0,0
.stabs "char:t2=r2;-32768;32767;",128,0,0,0
.stabs "long int:t3=r3;-2147483648;2147483647;",128,0,0,0
.stabs "unsigned int:t4=r4;0;65535;",128,0,0,0
.stabs "long unsigned int:t5=r5;0;4294967295;",128,0,0,0
.stabs "long long int:t6=r1;4;0;",128,0,0,0
.stabs "long long unsigned int:t7=r1;4;0;",128,0,0,0
.stabs "short int:t8=r8;-32768;32767;",128,0,0,0
.stabs "short unsigned int:t9=r9;0;65535;",128,0,0,0
.stabs "signed char:t10=r10;-32768;32767;",128,0,0,0
.stabs "unsigned char:t11=r11;0;65535;",128,0,0,0
.stabs "float:t12=r1;2;0;",128,0,0,0
.stabs "double:t13=r1;4;0;",128,0,0,0
.stabs "long double:t14=r1;4;0;",128,0,0,0
.stabs "complex float:t15=r1;4;0;",128,0,0,0
.stabs "complex double:t16=r1;8;0;",128,0,0,0
.stabs "complex long double:t17=r1;8;0;",128,0,0,0
.stabs "void:t18=18",128,0,0,0
.code
	     .stabs "main:F18",36,0,0,_main

	// Program Unit: main
.public	_main
_main: .proc	
	     .stabn 0xa6,0,0,3
	// base = 0
	// index = 2
	// old_frame_pointer = 3
	// return_address = 4
//   1  void main(void)
//   2  {

LM1:
	     .stabn 68,0,2,LM1-_main
BB1_PU0:	// 0x0
// BB:1 cycle count: 12
	     push BP to [SP]          	// [0:2]  
	     SP = SP - 3              	// [2:2]  
	     BP = SP + 1              	// [3:2]  
LBB2:
//   3      volatile unsigned short *base = (volatile unsigned short *)0x007300;

LM2:
	     .stabn 68,0,3,LM2-_main
	     R3 = 29440               	// [5:3]  
	     R4 = 0                   	// [7:3]  
	     [BP + 0] = R3            	// [8:3]  base
	     [BP + 1] = R4            	// [9:3]  base+1
//   4      unsigned int index;
//   5  
//   6      for (index = 0; index < 256; index++)

LM3:
	     .stabn 68,0,6,LM3-_main
	     R4 = 0                   	// [10:6]  
	     [BP + 2] = R4            	// [11:6]  index
L_0_3:	// 0xb
// BB:2 cycle count: 8
	     R4 = [BP + 2]            	// [0:6]  index
	     cmp R4, 255              	// [2:6]  
	     ja L_0_4                 	// [4:6]  
BB3_PU0:	// 0xf
// BB:3 cycle count: 12
//   7      {
//   8          base[index] = 0x7FFF;

LM4:
	     .stabn 68,0,8,LM4-_main
	     R2 = 32767               	// [0:8]  
	     R3 = [BP + 2]            	// [2:8]  index
	     R4 = 0                   	// [4:8]  
	     R3 = R3 + [BP + 0]       	// [5:8]  base
	     R4 = R4 + [BP + 1], Carry	// [7:8]  base+1
	     DS = R4                  	// [9:8]  
	     DS:[R3] = R2             	// [10:8]  
Lt_0_1:	// 0x17
// BB:4 cycle count: 8

LM5:
	     .stabn 68,0,6,LM5-_main
	     R4 = [BP + 2]            	// [0:6]  index
	     R4 = R4 + 1              	// [2:6]  
	     [BP + 2] = R4            	// [3:6]  index
	     jmp L_0_3                	// [4:6]  
L_0_4:	// 0x1b
L_0_5:	// 0x1b
Lt_0_2:	// 0x1b
// BB:5 cycle count: 4
//   9      }
//  10  
//  11      for (;;)

LM6:
	     .stabn 68,0,11,LM6-_main
	     jmp L_0_5                	// [0:11]  
BB6_PU0:	// 0x1c
// BB:6 cycle count: 6
	     SP = SP + 3              	// [0:11]  
	     pop BP, PC from [SP]     	// [1:11]  
LBE2:
	.endp	
	     .stabn 192,0,0,LBB2-_main
	     .stabs "base:19=*9",128,0,0,0
	     .stabs "index:4",128,0,0,2
	     .stabn 224,0,0,LBE2-_main
LME1:
	     .stabf LME1-_main
