void main(void)
{
    volatile unsigned short *base = (volatile unsigned short *)0x007300;
    unsigned int index;

    for (index = 0; index < 256; index++)
    {
        base[index] = 0x7FFF;
    }

    for (;;)
    {
    }
}