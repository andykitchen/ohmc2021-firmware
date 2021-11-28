#include <generated/csr.h>

#include "i2c.h"

#include "util.h"

#define START 0x1
#define STOP  0x2

#define ACK   0x0
#define NACK  0x1

static void nop_delay(void);
static int  status_error_code(int status);
static int  i2c_wait_tip(void);
static void i2c_tx_char(int *status, int tx, int sta);
static int  i2c_rx_char(int *status, int nack, int stop);

/* initialize i2c */
void NOINLINE i2c_init(void) {
	int flags;

	i2c1_core_reset_reset_write(1);

	/*
	setup clock prescaler...
	clock prescaler formula is apparently (clk_htz / (5*i2c_htz)) - 1
	e.g. (12e6 / (5*100e3)) - 1 == 23
	*/
	i2c1_prescale_write(23);

	flags = 0;
	flags = i2c1_control_ien_replace(flags, 0);  /* disable interrupts from the core */
	flags = i2c1_control_en_replace(flags, 1);   /* enable the core */

	/* write control flags */
	i2c1_control_write(flags);
}


#define STATUS_ACK_ARB_MASK ((1<<CSR_I2C1_STATUS_RXACK_OFFSET) | (1<<CSR_I2C1_STATUS_ARBLOST_OFFSET))
#define STATUS_ARBLOST_MASK (1<<CSR_I2C1_STATUS_ARBLOST_OFFSET)

#define CHECK_ACK(status)  if (status & STATUS_ACK_ARB_MASK) { goto error; }
#define CHECK_ARB(status)  if (status & STATUS_ARBLOST_MASK) { goto error; }

/* do i2c read transaction
 * @addr i2c device address
 * @tx0  first byte of read address
 * @tx1  second byte of read address
 * @err_status raw status register when error occured
 *
 * @return 16-bits of value read or error code less than zero:
 * -   ENACK got NACK when expecting ACK
 * -   EARB  lost bus arbitration
 * -   EUNK  other unknown error
 */
int i2c_read_txn(int addr, int tx0, int tx1, int *err_status) {
	int status;
	int rx0, rx1;

	/* send address in write mode with START */
	i2c_tx_char(&status, addr<<1, START);
	CHECK_ACK(status);

	/* send first data byte */
	i2c_tx_char(&status, tx0, 0);
	CHECK_ACK(status);

	/* optionally second data byte if non-negative */
	if (tx1 >= 0) {
		i2c_tx_char(&status, tx1, 0);
		CHECK_ACK(status);
	}

	/* send address in read mode and RESTART */
	i2c_tx_char(&status, (addr<<1) + 1, START);
	CHECK_ACK(status);

	/* receive with ACK */
	rx0 = i2c_rx_char(&status, ACK, 0);
	CHECK_ARB(status);

	/* receive with NACK and STOP */
	rx1 = i2c_rx_char(&status, NACK, STOP);
	CHECK_ARB(status);

	/* the i2c devices we are dealing with send 16-bit values data big-endian */
	return (rx0<<8) + rx1;

error:
	/* after a transfer error store the last status and return an error code */
	if (err_status)
		*err_status = status;

	return status_error_code(status);
}

/* See MCP47CXBXX datasheet Figure 7-9 pg. 84: General Call Reset Command */
int i2c_general_call_reset(void) {
	int status;

	/* send I2C general call address */
	i2c_tx_char(&status, 0x00, START);
	CHECK_ACK(status);

	/* send I2C general call reset command */
	i2c_tx_char(&status, 0x06, STOP);
	CHECK_ACK(status);

	return 0;

error:
	return status_error_code(status);
}

static void nop_delay(void) {
	for (int i = 23*8; i >= 0; i--)
		asm volatile ( "nop" );
}

static int status_error_code(int status) {
	if (i2c1_status_rxack_extract(status))
		return ENACK;
	else if (i2c1_status_arblost_extract(status))
		return EARB;
	else
		return EUNK;
}

/* wait for a pending i2c transfer to complete */
static int i2c_wait_tip(void) {
	int status;

	do {
		nop_delay();
		status = i2c1_status_read();
	}
	while (i2c1_status_tip_extract(status));

	return status;
}

/* transmit a single byte
 * @status  status out
 * @tx      byte to transfer
 * @sta_sto generate START / RESTART / STOP condition
 */
static void i2c_tx_char(int *status, int tx, int sta_sto) {
	int cmd;

	tx &= 0xff;  /* mask out only 8 low bits of tx data */

	i2c1_txr_write(tx);  /* setup tx data register */

	cmd = 0;
	cmd = i2c1_command_wr_replace(cmd, 1);                /* write command bit */
	cmd = i2c1_command_sta_replace(cmd, sta_sto & START); /* generate START condition? */
	cmd = i2c1_command_sto_replace(cmd, sta_sto & STOP);  /* generate STOP condition? */
	i2c1_command_write(cmd);                              /* send address byte */

	/* wait until transfer is complete */
	*status = i2c_wait_tip();
}

/* receive a single byte
 * @status status out
 * @nack   generate NACK (otherwise generate ACK)
 * @stop   generate STOP condition
 */
static int i2c_rx_char(int *status, int nack, int sto) {
	int cmd;

	cmd = 0;
	cmd = i2c1_command_rd_replace(cmd, 1);           /* read command bit */
	cmd = i2c1_command_ack_replace(cmd, nack);       /* generate ACK? */
	cmd = i2c1_command_sto_replace(cmd, sto & STOP); /* generate STOP condition? */
	i2c1_command_write(cmd);                         /* send address byte */

	/* wait until transfer is complete */
	*status = i2c_wait_tip();

	/* return value in rxr register */
	return i2c1_rxr_read();
}
