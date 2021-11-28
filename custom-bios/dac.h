#ifndef ROCKLING_DAC_H_
#define ROCKLING_DAC_H_

#define DAC_I2C_ADDR  0x62
#define DAC_ID_CMD    0xd6

int dac_read_txn(int tx);
int dac_write_txn(int tx, int data);

#endif
