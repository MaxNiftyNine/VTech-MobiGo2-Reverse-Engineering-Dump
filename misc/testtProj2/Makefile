#####################################################################
#																	 
#	Created by u'nSP IDE V4.1.1		19:52:31	03/18/26
#
#####################################################################




COMPILEDATE = %date:~0,4%%date:~5,2%%date:~8,2%

COMPILEDATE_ = %date:~0,4%-%date:~5,2%-%date:~8,2%

COMPILETIME = %time:~0,2%%time:~3,2%%time:~6,2%

PRJDIR	= C:\Users\Max\Desktop\testtProj2

APPDIR	= C:\PROGRA~2\GENERA~1\UNSPID~1.1

USERDIR	= C:\Users\Max\AppData\Roaming\Generalplus\unSPIDE

OUTDIR	= .\Debug

MK	= $(APPDIR)\make

CC	= "$(APPDIR)\toolchain\udocc"

AS	= "$(APPDIR)\toolchain\xasm16"

LD	= "$(APPDIR)\toolchain\xlink16"

LDIRAM0	= "$(APPDIR)\toolchain\xlink16_GPCE3_IRAM0"

AR	= "$(APPDIR)\toolchain\xlib16"

RESC	= "$(APPDIR)\toolchain\resc"

RM	= del	/F	1>NUL	2>NUL

STRIP	= "$(APPDIR)\toolchain\stripper"

S37STRIP	= "$(APPDIR)\toolchain\BinaryFileStripper"

RD	= rd /S /Q

GETCHKSUM	= "$(APPDIR)\toolchain\GetChecksum"

HDBPACKER	= "$(APPDIR)\toolchain\HDBPacker"

BOOTPACKER	= "$(APPDIR)\toolchain\BootPacker" 

LIKMODIFIER	= "$(APPDIR)\toolchain\unSP_LIKModifier" 

INCLUDES	= -I"C:/Users/Max/Desktop/testtProj2" -I"C:/Program Files (x86)/Generalplus/unSPIDE_4.1.1/library/include" -I"C:/Program Files (x86)/Generalplus/unSPIDE_4.1.1/library/include/sys"

BODY	= -body GPL16250VA_CS0Flash -nobdy -bfile "C:\Users\Max\Desktop\testtProj2\testtProj2.bdy"

BODYFILE	= "C:\Users\Max\Desktop\testtProj2\testtProj2.bdy" 

BINFILE	= "$(OUTDIR)\testtProj2.S37"

PRJNAME = testtProj2

BODYNAME = GPL16250VA_CS0Flash

SWTOOLID = 0x0008

IDEBUILDVER = 0x04010100

BINFILENOEXT	= $(OUTDIR)\testtProj2

ARYFILE	= "$(OUTDIR)\testtProj2.ary"

SBMFILE	= "$(OUTDIR)\testtProj2.sbm"

OPT	= -S -gstabs -Wall -mglobal-var-iram -mISA=2.0

ASFLAGS	= -t4 -d -sr

CASFLAGS	= -t4 -sr -wpop

CFLAGS	= $(OPT) -B$(APPDIR)\toolchain\ $(INCLUDES) 

BINTYPE	= -as

LDFLAGS	=  -initdata

EXTRAFLAGS	= 


OBJFILES	= \
	"$(OUTDIR)\main.obj" \
	"$(OUTDIR)\Resource.obj" 

"$(OUTDIR)\main.asm": "C:\Users\Max\Desktop\testtProj2\main.c" 
	set PATH="$(APPDIR)\toolchain\";%PATH% & \
	$(CC) $(CFLAGS) -o "$(OUTDIR)\main.asm" "C:/Users/Max/Desktop/testtProj2/main.c" 

"$(OUTDIR)\main.obj": "$(OUTDIR)\main.asm"
	$(AS) $(CASFLAGS) $(INCLUDES) -o "$(OUTDIR)\main.obj" "$(OUTDIR)\main.asm" 

"$(OUTDIR)\Resource.obj": "C:\Users\Max\Desktop\testtProj2\Resource.asm" 
	$(AS) $(ASFLAGS) $(INCLUDES) -o "$(OUTDIR)\Resource.obj" "C:\Users\Max\Desktop\testtProj2\Resource.asm" 


.SUFFIXES : .c .asm .obj .s37 .tsk .res

all :	 BEFOREBUILD "$(OUTDIR)" $(BINFILE)

BEFOREBUILD :

"$(OUTDIR)" :
	@if not exist "$(OUTDIR)\$(NULL)" mkdir "$(OUTDIR)"

$(BINFILE) : $(OBJFILES) 
	$(LD) $(BINTYPE) $(ARYFILE) $(BINFILE) $(LDFLAGS) $(BODY) $(EXTRAFLAGS) -undefined-opt __TgP190708CM -undefined-opt __TgP190708CL -undefined-opt __TgP190708M

compile :	 $(OBJFILES)

CLEANFILES = \
	"$(OUTDIR)\main.obj" \
	"$(OUTDIR)\main.lst" \
	"$(OUTDIR)\main.asm" \
	"$(OUTDIR)\Resource.obj" \
	"$(OUTDIR)\Resource.lst" \
	"$(BINFILENOEXT).s37" \
	"$(BINFILENOEXT).tsk" \
	"$(BINFILENOEXT)_SPI.bin*" \
	"$(BINFILENOEXT).hdb" \
	"$(BINFILENOEXT).lod" \
	"$(BINFILENOEXT).map" \
	"$(BINFILENOEXT).sbm" \
	"$(BINFILENOEXT).sym" \
	"$(BINFILENOEXT).smy" \
	$(SBMFILE)

clean :
	$(RM) $(wordlist 1,15,$(CLEANFILES))

.c.asm:
	$(CC) $(CFLAGS) $(INCLUDES) -o "$(OUTDIR)\$@" $<

.asm.obj:
	$(AS) $(ASFLAGS) $(INCLUDES) -o "$(OUTDIR)\$@" $<

