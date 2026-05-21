/******************************************************************************
* Copyright (C) 2023 Advanced Micro Devices, Inc. All Rights Reserved.
* SPDX-License-Identifier: MIT
******************************************************************************/

#include <stdint.h>

#include "lwip/dhcp.h"
#include "lwip/err.h"
#include "lwip/init.h"
#include "lwip/inet.h"
#include "lwip/netif.h"
#include "lwip/pbuf.h"
#include "lwip/udp.h"
#include "netif/xadapter.h"
#include "platform.h"
#include "sleep.h"
#include "xaxidma.h"
#include "xaxidma_hw.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xstatus.h"

#define DMA_BASEADDR            XPAR_XAXIDMA_0_BASEADDR
#define EMAC_BASEADDR           XPAR_XEMACPS_0_BASEADDR

/*
 * Campus-network mode: the board obtains its own IP address by DHCP, then
 * streams UDP packets to the PC address below.
 */
#define PC_IP_ADDR              "10.16.58.111"
#define PC_UDP_PORT             5001U

/*
 * PL sends one AXI-Stream packet every 512 16-bit words.  In DDC mode that is
 * 256 interleaved IQ samples.  The DMA S2MM transfer length must match TLAST.
 */
#define SDR_WORDS_PER_PACKET    512U
#define SDR_IQ_SAMPLES_PER_PACKET 256U
#define SDR_BYTES_PER_SAMPLE    2U
#define SDR_PACKET_BYTES        (SDR_WORDS_PER_PACKET * SDR_BYTES_PER_SAMPLE)
#define SDR_UDP_MAGIC           0x52534648U
#define SDR_UDP_HEADER_BYTES    16U
#define SDR_UDP_PACKET_BYTES    (SDR_UDP_HEADER_BYTES + SDR_PACKET_BYTES)
#define SDR_UDP_VERSION         1U
#define SDR_SAMPLE_FORMAT_S16   1U
#define SDR_SAMPLE_FORMAT_IQ_S16 2U

#define SDR_PRINT_INTERVAL      1024U
#define DMA_TIMEOUT_US          1000000U
#define DMA_RESET_TIMEOUT       10000U
#define UDP_SEND_RETRIES        10U
#define UDP_HELLO_PACKETS       20U
#define DHCP_TIMEOUT_MS         60000U

static XAxiDma AxiDma;
static struct netif NetIf;
static struct udp_pcb *UdpPcb;
static uint8_t RxBuffer[SDR_PACKET_BYTES] __attribute__((aligned(64)));
static uint8_t TxBuffer[SDR_UDP_PACKET_BYTES] __attribute__((aligned(64)));

typedef struct __attribute__((packed)) {
    uint32_t Magic;
    uint32_t Sequence;
    uint16_t HeaderBytes;
    uint16_t SampleCount;
    uint16_t SampleFormat;
    uint16_t PayloadBytes;
} SdrUdpHeader;

static int InitDma(void);
static int InitNetwork(void);
static int InitUdp(void);
static int CaptureOnePacket(void);
static int SendUdpPacket(const uint8_t *Data, uint16_t Length);
static int SendSdrUdpPacket(uint32_t Sequence);
static void SendUdpHelloPackets(void);
static int WaitForS2mmDone(void);
static int ResetDma(void);
static void PollNetwork(void);
static void PrintIp(const char *Name, const ip_addr_t *Ip);
static void PrintS2mmStatus(const char *Tag);
static void GetPacketStats(int *Min, int *Max, int *Avg, uint32_t *Changed);

int main(void)
{
    int Status;
    uint32_t SentPackets = 0U;
    uint32_t DroppedPackets = 0U;
    uint32_t Sequence = 0U;

    init_platform();

    xil_printf("\r\nHF SDR ADC DMA UDP stream test\r\n");
    xil_printf("DMA base: 0x%08x\r\n", (unsigned int)DMA_BASEADDR);
    xil_printf("EMAC base: 0x%08x\r\n", (unsigned int)EMAC_BASEADDR);
    xil_printf("Packet: %u IQ samples, %u bytes\r\n",
               (unsigned int)SDR_IQ_SAMPLES_PER_PACKET,
               (unsigned int)SDR_PACKET_BYTES);
    xil_printf("UDP frame: %u-byte header + %u-byte payload\r\n",
               (unsigned int)SDR_UDP_HEADER_BYTES,
               (unsigned int)SDR_PACKET_BYTES);

    Status = InitDma();
    if (Status != XST_SUCCESS) {
        xil_printf("DMA init failed\r\n");
        cleanup_platform();
        return XST_FAILURE;
    }

    Status = InitNetwork();
    if (Status != XST_SUCCESS) {
        xil_printf("Network init failed\r\n");
        cleanup_platform();
        return XST_FAILURE;
    }

    Status = InitUdp();
    if (Status != XST_SUCCESS) {
        xil_printf("UDP init failed\r\n");
        cleanup_platform();
        return XST_FAILURE;
    }

    xil_printf("Streaming ADC packets to %s:%u\r\n",
               PC_IP_ADDR, (unsigned int)PC_UDP_PORT);
    SendUdpHelloPackets();

    while (1) {
        PollNetwork();

        Status = CaptureOnePacket();
        if (Status != XST_SUCCESS) {
            xil_printf("Capture failed\r\n");
            PrintS2mmStatus("fail");
            cleanup_platform();
            return XST_FAILURE;
        }

        Status = SendSdrUdpPacket(Sequence);
        if (Status == XST_SUCCESS) {
            SentPackets++;
            Sequence++;
        } else {
            DroppedPackets++;
        }

        if ((SentPackets != 0U) &&
            ((SentPackets % SDR_PRINT_INTERVAL) == 0U)) {
            int Min;
            int Max;
            int Avg;
            uint32_t Changed;

            GetPacketStats(&Min, &Max, &Avg, &Changed);
            xil_printf("sent=%u dropped=%u min=%d max=%d avg=%d changed=%u\r\n",
                       (unsigned int)SentPackets,
                       (unsigned int)DroppedPackets,
                       Min, Max, Avg, (unsigned int)Changed);
        }
    }
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

static int InitNetwork(void)
{
    err_t Err;
    unsigned char MacAddress[] = {
        0x00U, 0x0aU, 0x35U, 0x00U, 0x01U, 0x02U
    };

    lwip_init();

    if (xemac_add(&NetIf, NULL, NULL, NULL,
                  MacAddress, EMAC_BASEADDR) == NULL) {
        xil_printf("xemac_add failed\r\n");
        return XST_FAILURE;
    }

    netif_set_default(&NetIf);
    netif_set_up(&NetIf);

    xil_printf("MAC: %02x:%02x:%02x:%02x:%02x:%02x\r\n",
               MacAddress[0], MacAddress[1], MacAddress[2],
               MacAddress[3], MacAddress[4], MacAddress[5]);
    xil_printf("Starting DHCP...\r\n");
    Err = dhcp_start(&NetIf);
    if (Err != ERR_OK) {
        xil_printf("dhcp_start failed: %d\r\n", (int)Err);
        return XST_FAILURE;
    }

    for (uint32_t ElapsedMs = 0U; ElapsedMs < DHCP_TIMEOUT_MS; ElapsedMs++) {
        PollNetwork();

        if (NetIf.ip_addr.addr != 0U) {
            break;
        }

        if (((ElapsedMs + 1U) % 500U) == 0U) {
            dhcp_fine_tmr();
        }

        if (((ElapsedMs + 1U) % 60000U) == 0U) {
            dhcp_coarse_tmr();
        }

        if (((ElapsedMs + 1U) % 5000U) == 0U) {
            xil_printf("Waiting DHCP... %u ms\r\n",
                       (unsigned int)(ElapsedMs + 1U));
        }

        usleep(1000U);
    }

    if (NetIf.ip_addr.addr == 0U) {
        xil_printf("DHCP timeout after %u ms\r\n",
                   (unsigned int)DHCP_TIMEOUT_MS);
        return XST_FAILURE;
    }

    PrintIp("Board IP", &NetIf.ip_addr);
    PrintIp("Netmask ", &NetIf.netmask);
    PrintIp("Gateway ", &NetIf.gw);

    return XST_SUCCESS;
}

static int InitUdp(void)
{
    ip_addr_t PcIp;
    err_t Err;

    if (!inet_aton(PC_IP_ADDR, &PcIp)) {
        xil_printf("Invalid PC IP setting\r\n");
        return XST_FAILURE;
    }

    UdpPcb = udp_new();
    if (UdpPcb == NULL) {
        xil_printf("udp_new failed\r\n");
        return XST_FAILURE;
    }

    Err = udp_connect(UdpPcb, &PcIp, PC_UDP_PORT);
    if (Err != ERR_OK) {
        xil_printf("udp_connect failed: %d\r\n", (int)Err);
        udp_remove(UdpPcb);
        UdpPcb = NULL;
        return XST_FAILURE;
    }

    return XST_SUCCESS;
}

static int CaptureOnePacket(void)
{
    int Status;

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

static int SendUdpPacket(const uint8_t *Data, uint16_t Length)
{
    struct pbuf *Packet;
    err_t Err;

    Packet = pbuf_alloc(PBUF_TRANSPORT, Length, PBUF_POOL);
    if (Packet == NULL) {
        xil_printf("pbuf_alloc failed\r\n");
        return XST_FAILURE;
    }

    Err = pbuf_take(Packet, Data, Length);
    if (Err != ERR_OK) {
        xil_printf("pbuf_take failed: %d\r\n", (int)Err);
        pbuf_free(Packet);
        return XST_FAILURE;
    }

    for (uint32_t Retry = 0U; Retry < UDP_SEND_RETRIES; Retry++) {
        Err = udp_send(UdpPcb, Packet);
        if (Err == ERR_OK) {
            pbuf_free(Packet);
            return XST_SUCCESS;
        }

        PollNetwork();
        usleep(100U);
    }

    xil_printf("udp_send failed: %d\r\n", (int)Err);
    pbuf_free(Packet);
    return XST_FAILURE;
}

static int SendSdrUdpPacket(uint32_t Sequence)
{
    SdrUdpHeader *Header = (SdrUdpHeader *)TxBuffer;

    Header->Magic = SDR_UDP_MAGIC;
    Header->Sequence = Sequence;
    Header->HeaderBytes = SDR_UDP_HEADER_BYTES;
    Header->SampleCount = SDR_IQ_SAMPLES_PER_PACKET;
    Header->SampleFormat = SDR_SAMPLE_FORMAT_IQ_S16;
    Header->PayloadBytes = SDR_PACKET_BYTES;

    for (uint32_t Index = 0U; Index < SDR_PACKET_BYTES; Index++) {
        TxBuffer[SDR_UDP_HEADER_BYTES + Index] = RxBuffer[Index];
    }

    return SendUdpPacket(TxBuffer, SDR_UDP_PACKET_BYTES);
}

static void SendUdpHelloPackets(void)
{
    static const uint8_t Hello[] = "HF_SDR_UDP_HELLO";
    uint32_t Sent = 0U;

    for (uint32_t Index = 0U; Index < UDP_HELLO_PACKETS; Index++) {
        PollNetwork();

        if (SendUdpPacket(Hello, (uint16_t)(sizeof(Hello) - 1U)) == XST_SUCCESS) {
            Sent++;
        }

        usleep(100000U);
    }

    xil_printf("UDP hello packets sent: %u/%u\r\n",
               (unsigned int)Sent, (unsigned int)UDP_HELLO_PACKETS);
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

static void PollNetwork(void)
{
    (void)xemacif_input(&NetIf);
}

static void PrintIp(const char *Name, const ip_addr_t *Ip)
{
    xil_printf("%s: %d.%d.%d.%d\r\n",
               Name,
               ip4_addr1(Ip), ip4_addr2(Ip),
               ip4_addr3(Ip), ip4_addr4(Ip));
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

static void GetPacketStats(int *Min, int *Max, int *Avg, uint32_t *Changed)
{
    const int16_t *Samples = (const int16_t *)RxBuffer;
    int Sum = 0;

    *Min = 32767;
    *Max = -32768;
    *Changed = 0U;

    for (uint32_t Index = 0U; Index < SDR_WORDS_PER_PACKET; Index++) {
        int Value = (int)Samples[Index];

        if (Value < *Min) {
            *Min = Value;
        }

        if (Value > *Max) {
            *Max = Value;
        }

        if ((Index > 0U) && (Samples[Index] != Samples[Index - 1U])) {
            (*Changed)++;
        }

        Sum += Value;
    }

    *Avg = Sum / (int)SDR_WORDS_PER_PACKET;
}
