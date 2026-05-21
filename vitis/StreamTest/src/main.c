/******************************************************************************
* Copyright (C) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
* SPDX-License-Identifier: MIT
******************************************************************************/

#include <stdint.h>

#include "platform.h"
#include "sleep.h"
#include "xaxidma.h"
#include "xaxidma_hw.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xstatus.h"

#define DMA_BASEADDR            XPAR_XAXIDMA_0_BASEADDR

/*
 * PL sends one AXI-Stream packet every 512 samples.  The DMA S2MM transfer
 * length must match that TLAST cadence, otherwise S2MM can stop early or wait.
 */
#define SDR_SAMPLES_PER_PACKET  512U
#define SDR_BYTES_PER_SAMPLE    2U
#define SDR_PACKET_BYTES        (SDR_SAMPLES_PER_PACKET * SDR_BYTES_PER_SAMPLE)

#define SDR_TEST_PACKETS        32U
#define SDR_PRINT_SAMPLES       16U
#define DMA_TIMEOUT_US          1000000U
#define DMA_RESET_TIMEOUT       10000U

static XAxiDma AxiDma;
static uint8_t RxBuffer[SDR_PACKET_BYTES] __attribute__((aligned(64)));

static int InitDma(void);
static int CaptureOnePacket(void);
static int WaitForS2mmDone(void);
static int ResetDma(void);
static void PrintS2mmStatus(const char *Tag);
static void PrintPacketStats(uint32_t PacketIndex);

int main(void)
{
    int Status;

    init_platform();

    xil_printf("\r\nHF SDR ADC DMA receive test\r\n");
    xil_printf("DMA base: 0x%08x\r\n", (unsigned int)DMA_BASEADDR);
    xil_printf("Packet: %u samples, %u bytes\r\n",
               (unsigned int)SDR_SAMPLES_PER_PACKET,
               (unsigned int)SDR_PACKET_BYTES);

    Status = InitDma();
    if (Status != XST_SUCCESS) {
        xil_printf("DMA init failed\r\n");
        cleanup_platform();
        return XST_FAILURE;
    }

    for (uint32_t Packet = 0U; Packet < SDR_TEST_PACKETS; Packet++) {
        Status = CaptureOnePacket();
        if (Status != XST_SUCCESS) {
            xil_printf("Capture failed at packet %u\r\n", (unsigned int)Packet);
            PrintS2mmStatus("fail");
            cleanup_platform();
            return XST_FAILURE;
        }

        PrintPacketStats(Packet);
    }

    xil_printf("DMA receive test finished\r\n");
    cleanup_platform();
    return XST_SUCCESS;
}

static int InitDma(void)
{
    XAxiDma_Config *Config;
    int Status;

    Config = XAxiDma_LookupConfig(DMA_BASEADDR);
    if (Config == NULL) {
        xil_printf("No AXI DMA config found at 0x%08x\r\n",
                   (unsigned int)DMA_BASEADDR);
        return XST_FAILURE;
    }

    Status = XAxiDma_CfgInitialize(&AxiDma, Config);
    if (Status != XST_SUCCESS) {
        xil_printf("XAxiDma_CfgInitialize failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    if (XAxiDma_HasSg(&AxiDma)) {
        xil_printf("DMA is configured as scatter-gather, expected simple mode\r\n");
        return XST_FAILURE;
    }

#if !XPAR_XAXIDMA_0_INCLUDE_S2MM
    xil_printf("DMA S2MM channel is not enabled in hardware\r\n");
    return XST_FAILURE;
#endif

    XAxiDma_IntrDisable(&AxiDma, XAXIDMA_IRQ_ALL_MASK,
                        XAXIDMA_DEVICE_TO_DMA);

    Status = ResetDma();
    if (Status != XST_SUCCESS) {
        xil_printf("DMA reset timeout\r\n");
        return XST_FAILURE;
    }

    PrintS2mmStatus("init");
    return XST_SUCCESS;
}

static int CaptureOnePacket(void)
{
    int Status;

    for (uint32_t Index = 0U; Index < SDR_PACKET_BYTES; Index++) {
        RxBuffer[Index] = 0xA5U;
    }

    Xil_DCacheFlushRange((UINTPTR)RxBuffer, SDR_PACKET_BYTES);

    Status = XAxiDma_SimpleTransfer(&AxiDma, (UINTPTR)RxBuffer,
                                    SDR_PACKET_BYTES,
                                    XAXIDMA_DEVICE_TO_DMA);
    if (Status != XST_SUCCESS) {
        xil_printf("XAxiDma_SimpleTransfer failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = WaitForS2mmDone();
    if (Status != XST_SUCCESS) {
        return XST_FAILURE;
    }

    Xil_DCacheInvalidateRange((UINTPTR)RxBuffer, SDR_PACKET_BYTES);
    return XST_SUCCESS;
}

static int WaitForS2mmDone(void)
{
    for (uint32_t Wait = 0U; Wait < DMA_TIMEOUT_US; Wait++) {
        u32 Status = XAxiDma_ReadReg(AxiDma.RegBase + XAXIDMA_RX_OFFSET,
                                     XAXIDMA_SR_OFFSET);

        if ((Status & XAXIDMA_ERR_ALL_MASK) != 0U) {
            xil_printf("S2MM error, DMASR=0x%08x\r\n", (unsigned int)Status);
            (void)ResetDma();
            return XST_FAILURE;
        }

        if (!XAxiDma_Busy(&AxiDma, XAXIDMA_DEVICE_TO_DMA)) {
            return XST_SUCCESS;
        }

        usleep(1U);
    }

    xil_printf("S2MM timeout\r\n");
    return XST_FAILURE;
}

static int ResetDma(void)
{
    XAxiDma_Reset(&AxiDma);

    for (uint32_t Wait = 0U; Wait < DMA_RESET_TIMEOUT; Wait++) {
        if (XAxiDma_ResetIsDone(&AxiDma)) {
            return XST_SUCCESS;
        }
    }

    return XST_FAILURE;
}

static void PrintS2mmStatus(const char *Tag)
{
    u32 Status = XAxiDma_ReadReg(AxiDma.RegBase + XAXIDMA_RX_OFFSET,
                                 XAXIDMA_SR_OFFSET);

    xil_printf("[%s] S2MM_DMASR=0x%08x halted=%u idle=%u err=0x%03x\r\n",
               Tag,
               (unsigned int)Status,
               (unsigned int)((Status & XAXIDMA_HALTED_MASK) ? 1U : 0U),
               (unsigned int)((Status & XAXIDMA_IDLE_MASK) ? 1U : 0U),
               (unsigned int)(Status & XAXIDMA_ERR_ALL_MASK));
}

static void PrintPacketStats(uint32_t PacketIndex)
{
    const int16_t *Samples = (const int16_t *)RxBuffer;
    int Min = 32767;
    int Max = -32768;
    int Sum = 0;
    uint32_t Changed = 0U;

    for (uint32_t Index = 0U; Index < SDR_SAMPLES_PER_PACKET; Index++) {
        int Value = (int)Samples[Index];

        if (Value < Min) {
            Min = Value;
        }

        if (Value > Max) {
            Max = Value;
        }

        if ((Index > 0U) && (Samples[Index] != Samples[Index - 1U])) {
            Changed++;
        }

        Sum += Value;
    }

    xil_printf("pkt %u: min=%d max=%d avg=%d changed=%u first:",
               (unsigned int)PacketIndex,
               Min,
               Max,
               Sum / (int)SDR_SAMPLES_PER_PACKET,
               (unsigned int)Changed);

    for (uint32_t Index = 0U; Index < SDR_PRINT_SAMPLES; Index++) {
        xil_printf(" %d", (int)Samples[Index]);
    }

    xil_printf("\r\n");
}
