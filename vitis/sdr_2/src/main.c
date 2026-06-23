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
#include "xil_io.h"
#include "xparameters.h"
#include "xstatus.h"

#define DMA_BASEADDR            XPAR_XAXIDMA_0_BASEADDR
#define EMAC_BASEADDR           XPAR_XEMACPS_0_BASEADDR

/*
 * Network mode:
 * - SDR_USE_DHCP = 1: campus/LAN mode, board obtains IP by DHCP.
 * - SDR_USE_DHCP = 0: direct-connect/static mode.
 *
 * Keep the PC address paired with the selected mode so switching only needs
 * this one macro.
 */
#define SDR_USE_DHCP            0

#define PC_IP_ADDR_DHCP         "10.16.25.167"
#define PC_IP_ADDR_STATIC       "192.168.1.100"

#define STATIC_IP_ADDR0         192U
#define STATIC_IP_ADDR1         168U
#define STATIC_IP_ADDR2         1U
#define STATIC_IP_ADDR3         10U
#define STATIC_NETMASK_ADDR0    255U
#define STATIC_NETMASK_ADDR1    255U
#define STATIC_NETMASK_ADDR2    255U
#define STATIC_NETMASK_ADDR3    0U
#define STATIC_GW_ADDR0         192U
#define STATIC_GW_ADDR1         168U
#define STATIC_GW_ADDR2         1U
#define STATIC_GW_ADDR3         1U

#if SDR_USE_DHCP
#define PC_IP_ADDR              PC_IP_ADDR_DHCP
#else
#define PC_IP_ADDR              PC_IP_ADDR_STATIC
#endif

#define PC_UDP_PORT             5001U
#define SDR_CTRL_UDP_PORT       5002U

/*
 * PL sends one AXI-Stream packet every 512 16-bit words.  In DDC mode that is
 * 256 interleaved IQ samples.  The DMA S2MM transfer length must match TLAST.
 */
#define SDR_WORDS_PER_PACKET    512U
#define SDR_IQ_SAMPLES_PER_PACKET 256U
#define SDR_BYTES_PER_SAMPLE    2U
#define SDR_PACKET_BYTES        (SDR_WORDS_PER_PACKET * SDR_BYTES_PER_SAMPLE)
#define SDR_UDP_MAGIC           0x52534648U
#define SDR_UDP_HEADER_BYTES    24U
#define SDR_UDP_PACKET_BYTES    (SDR_UDP_HEADER_BYTES + SDR_PACKET_BYTES)
#define SDR_UDP_VERSION         2U
#define SDR_SAMPLE_FORMAT_S16   1U
#define SDR_SAMPLE_FORMAT_IQ_S16 2U

#define SDR_RUNTIME_PRINTS      0U
#define SDR_PRINT_INTERVAL      32768U
#define SDR_SG_RX_BD_COUNT      128U
#define SDR_SG_BD_SPACE_BYTES   (SDR_SG_RX_BD_COUNT * XAXIDMA_BD_MINIMUM_ALIGNMENT)
#define UDP_SEND_RETRIES        10U
#define UDP_HELLO_PACKETS       20U
#define DHCP_TIMEOUT_MS         60000U
#define DMA_POLL_IDLE_US        50U
#define SDR_SG_SYNC_DROP_PRINTS 8U
#define SDR_STARTUP_DRAIN_PACKETS 512U
#define SDR_STARTUP_DRAIN_IDLE_LOOPS 256U

#define SDR_ADC_CLK_HZ          65000000ULL
#define SDR_TOTAL_DECIMATION    64ULL
#define SDR_IQ_SAMPLE_RATE_HZ   (SDR_ADC_CLK_HZ / SDR_TOTAL_DECIMATION)
#define SDR_CENTER_FREQ_HZ      4900000ULL
#define SDR_DDC_PHASE_REG       0x00U
#define SDR_DDC_VERSION_REG     0x04U
#define SDR_DDC_STATUS_REG      0x08U
#define SDR_DDC_COUNTER_REG     0x0CU
#define SDR_DDC_STATUS_FULL     0x00000001U
#define SDR_DDC_STATUS_FIFO_SHIFT 4U
#define SDR_DDC_STATUS_MAX_FIFO_SHIFT 18U
#define SDR_DDC_STATUS_FIFO_MASK 0x3fffU
#define SDR_DDC_DRAIN_LEVEL     16U

#if defined(XPAR_HF_SDR_TOP_0_BASEADDR)
#define DDC_CTRL_BASEADDR       XPAR_HF_SDR_TOP_0_BASEADDR
#elif defined(XPAR_HF_SDR_TOP_0_S_AXI_BASEADDR)
#define DDC_CTRL_BASEADDR       XPAR_HF_SDR_TOP_0_S_AXI_BASEADDR
#else
#define DDC_CTRL_BASEADDR       0x40000000U
#endif

static XAxiDma AxiDma;
static struct netif NetIf;
static struct udp_pcb *UdpPcb;
static struct udp_pcb *CtrlPcb;
static XAxiDma_BdRing *RxRing;
static uint8_t RxBdSpace[SDR_SG_BD_SPACE_BYTES]
    __attribute__((aligned(XAXIDMA_BD_MINIMUM_ALIGNMENT)));
static uint8_t RxBuffers[SDR_SG_RX_BD_COUNT][SDR_PACKET_BYTES]
    __attribute__((aligned(64)));
static uint8_t TxBuffer[SDR_UDP_PACKET_BYTES] __attribute__((aligned(64)));
static uint64_t DdcCenterFreqHz = SDR_CENTER_FREQ_HZ;

typedef struct __attribute__((packed)) {
    uint32_t Magic;
    uint32_t Sequence;
    uint16_t HeaderBytes;
    uint16_t SampleCount;
    uint16_t SampleFormat;
    uint16_t PayloadBytes;
    uint32_t CenterFreqHz;
    uint32_t SampleRateHz;
} SdrUdpHeader;

static int InitDma(void);
static int InitNetwork(void);
static int InitUdp(void);
static void ConfigureDdc(uint64_t FreqHz);
static void SetDdcFrequency(uint64_t FreqHz, const char *Tag);
static uint32_t CalcDdsPhaseInc(uint64_t FreqHz);
static uint64_t ParseUnsigned(const char *Text, uint32_t Length,
                              uint32_t *Consumed);
static void ControlUdpRecv(void *Arg, struct udp_pcb *Pcb, struct pbuf *Pbuf,
                           const ip_addr_t *Addr, u16_t Port);
static void SendControlReply(const ip_addr_t *Addr, u16_t Port,
                             const char *Text);
static int SetupRxBd(XAxiDma_Bd *BdPtr, uint8_t *Buffer);
static int PollRxPacket(uint8_t **Data, u32 *Length, XAxiDma_Bd **DoneBd);
static int RequeueRxBd(XAxiDma_Bd *DoneBd, uint8_t *Buffer);
static int SendUdpPacket(const uint8_t *Data, uint16_t Length);
static void PrepareSdrUdpPacket(uint32_t Sequence, const uint8_t *Payload);
static void SendUdpHelloPackets(void);
static void PollNetwork(void);
static void PrintIp(const char *Name, const ip_addr_t *Ip);
static void PrintS2mmStatus(const char *Tag);
static void PrintDdcStatus(const char *Tag);
static int DrainStartupDma(void);
#if SDR_RUNTIME_PRINTS
static void GetPacketStats(const uint8_t *Data, int *Min, int *Max, int *Avg,
                           uint32_t *Changed);
#endif

int main(void)
{
    int Status;
    uint32_t SentPackets = 0U;
    uint32_t DroppedPackets = 0U;
    uint32_t Sequence = 0U;
    uint32_t StreamSynced = 0U;
    uint32_t SyncDrops = 0U;
    uint32_t StatusClearedAfterDrain = 0U;

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
    xil_printf("DDC clock: %u Hz, decimation: %u, IQ rate: %u S/s\r\n",
               (unsigned int)SDR_ADC_CLK_HZ,
               (unsigned int)SDR_TOTAL_DECIMATION,
               (unsigned int)SDR_IQ_SAMPLE_RATE_HZ);
    ConfigureDdc(SDR_CENTER_FREQ_HZ);

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
    xil_printf("Listening for tune commands on UDP port %u\r\n",
               (unsigned int)SDR_CTRL_UDP_PORT);

    SendUdpHelloPackets();

    Status = DrainStartupDma();
    if (Status != XST_SUCCESS) {
        xil_printf("Startup DMA drain failed\r\n");
        cleanup_platform();
        return XST_FAILURE;
    }

    Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG, 1U);
#if SDR_RUNTIME_PRINTS
    PrintDdcStatus("stream-start");
#endif

    while (1) {
        uint8_t *PacketData;
        XAxiDma_Bd *DoneBd;
        u32 PacketLength;

        PollNetwork();

        Status = PollRxPacket(&PacketData, &PacketLength, &DoneBd);
        if (Status < 0) {
            xil_printf("Capture failed\r\n");
            PrintS2mmStatus("fail");
            cleanup_platform();
            return XST_FAILURE;
        }

        if (Status == 0) {
            usleep(DMA_POLL_IDLE_US);
            continue;
        }

        if (PacketLength != SDR_PACKET_BYTES) {
            if (SyncDrops < SDR_SG_SYNC_DROP_PRINTS) {
                xil_printf("SG sync drop length: %u\r\n",
                           (unsigned int)PacketLength);
            }
            SyncDrops++;
            DroppedPackets++;

            Status = RequeueRxBd(DoneBd, PacketData);
            if (Status != XST_SUCCESS) {
                xil_printf("RX BD requeue failed\r\n");
                PrintS2mmStatus("requeue");
                cleanup_platform();
                return XST_FAILURE;
            }
            continue;
        }

        if (StreamSynced == 0U) {
            StreamSynced = 1U;
#if SDR_RUNTIME_PRINTS
            xil_printf("SG stream synchronized after %u dropped packet(s)\r\n",
                       (unsigned int)SyncDrops);
            PrintDdcStatus("sync");
#endif
        }

        if (StatusClearedAfterDrain == 0U) {
            u32 DdcStatus = Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG);
            u32 FifoLevel = (DdcStatus >> SDR_DDC_STATUS_FIFO_SHIFT) &
                            SDR_DDC_STATUS_FIFO_MASK;

            if (((DdcStatus & SDR_DDC_STATUS_FULL) == 0U) &&
                (FifoLevel <= SDR_DDC_DRAIN_LEVEL)) {
                Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG, 1U);
                usleep(100U);
                StatusClearedAfterDrain = 1U;
#if SDR_RUNTIME_PRINTS
                PrintDdcStatus("drain-clear");
#endif
            }
        }

#if SDR_RUNTIME_PRINTS
        if ((SentPackets != 0U) &&
            ((SentPackets % SDR_PRINT_INTERVAL) == 0U)) {
            int Min;
            int Max;
            int Avg;
            uint32_t Changed;

            GetPacketStats(PacketData, &Min, &Max, &Avg, &Changed);
            xil_printf("sent=%u dropped=%u min=%d max=%d avg=%d changed=%u\r\n",
                       (unsigned int)SentPackets,
                       (unsigned int)DroppedPackets,
                       Min, Max, Avg, (unsigned int)Changed);
            PrintDdcStatus("ddc");
        }
#endif

        PrepareSdrUdpPacket(Sequence, PacketData);

        Status = RequeueRxBd(DoneBd, PacketData);
        if (Status != XST_SUCCESS) {
            xil_printf("RX BD requeue failed\r\n");
            PrintS2mmStatus("requeue");
            cleanup_platform();
            return XST_FAILURE;
        }

        Status = SendUdpPacket(TxBuffer, SDR_UDP_PACKET_BYTES);
        if (Status == XST_SUCCESS) {
            SentPackets++;
            Sequence++;
        } else {
            DroppedPackets++;
        }
    }
}

static int InitDma(void)
{
    XAxiDma_Config *Config;
    int Status;
    XAxiDma_Bd BdTemplate;
    XAxiDma_Bd *BdPtr;
    XAxiDma_Bd *BdCurPtr;
    u32 BdCount;

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

    if (!XAxiDma_HasSg(&AxiDma)) {
        xil_printf("DMA is configured as simple mode, expected scatter-gather\r\n");
        return XST_FAILURE;
    }

#if !XPAR_XAXIDMA_0_INCLUDE_S2MM
    xil_printf("DMA S2MM channel is not enabled in hardware\r\n");
    return XST_FAILURE;
#endif

    RxRing = XAxiDma_GetRxRing(&AxiDma);
    XAxiDma_BdRingIntDisable(RxRing, XAXIDMA_IRQ_ALL_MASK);
    XAxiDma_BdRingSetCoalesce(RxRing, 1, 0);

    BdCount = XAxiDma_BdRingCntCalc(XAXIDMA_BD_MINIMUM_ALIGNMENT,
                                    SDR_SG_BD_SPACE_BYTES);
    if (BdCount > SDR_SG_RX_BD_COUNT) {
        BdCount = SDR_SG_RX_BD_COUNT;
    }

    Status = XAxiDma_BdRingCreate(RxRing,
                                  (UINTPTR)RxBdSpace,
                                  (UINTPTR)RxBdSpace,
                                  XAXIDMA_BD_MINIMUM_ALIGNMENT,
                                  BdCount);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD ring create failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    XAxiDma_BdClear(&BdTemplate);
    Status = XAxiDma_BdRingClone(RxRing, &BdTemplate);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD ring clone failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = XAxiDma_BdRingAlloc(RxRing, (int)BdCount, &BdPtr);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD ring alloc failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    BdCurPtr = BdPtr;
    for (u32 Index = 0U; Index < BdCount; Index++) {
        Status = SetupRxBd(BdCurPtr, RxBuffers[Index]);
        if (Status != XST_SUCCESS) {
            xil_printf("RX BD setup failed at %u: %d\r\n",
                       (unsigned int)Index, Status);
            return XST_FAILURE;
        }
        BdCurPtr = (XAxiDma_Bd *)XAxiDma_BdRingNext(RxRing, BdCurPtr);
    }

    Status = XAxiDma_BdRingToHw(RxRing, (int)BdCount, BdPtr);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD submit failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = XAxiDma_BdRingStart(RxRing);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD ring start failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    xil_printf("DMA SG RX ring: %u BDs x %u bytes\r\n",
               (unsigned int)BdCount, (unsigned int)SDR_PACKET_BYTES);
    PrintS2mmStatus("init");
    return XST_SUCCESS;
}

static int InitNetwork(void)
{
#if SDR_USE_DHCP
    err_t Err;
#else
    ip_addr_t IpAddr;
    ip_addr_t Netmask;
    ip_addr_t Gateway;
#endif
    unsigned char MacAddress[] = {
        0x00U, 0x0aU, 0x35U, 0x00U, 0x01U, 0x02U
    };

    lwip_init();

#if SDR_USE_DHCP
    if (xemac_add(&NetIf, NULL, NULL, NULL,
                  MacAddress, EMAC_BASEADDR) == NULL) {
        xil_printf("xemac_add failed\r\n");
        return XST_FAILURE;
    }
#else
    IP4_ADDR(&IpAddr,
             STATIC_IP_ADDR0, STATIC_IP_ADDR1,
             STATIC_IP_ADDR2, STATIC_IP_ADDR3);
    IP4_ADDR(&Netmask,
             STATIC_NETMASK_ADDR0, STATIC_NETMASK_ADDR1,
             STATIC_NETMASK_ADDR2, STATIC_NETMASK_ADDR3);
    IP4_ADDR(&Gateway,
             STATIC_GW_ADDR0, STATIC_GW_ADDR1,
             STATIC_GW_ADDR2, STATIC_GW_ADDR3);

    if (xemac_add(&NetIf, &IpAddr, &Netmask, &Gateway,
                  MacAddress, EMAC_BASEADDR) == NULL) {
        xil_printf("xemac_add failed\r\n");
        return XST_FAILURE;
    }
#endif

    netif_set_default(&NetIf);
    netif_set_up(&NetIf);

    xil_printf("MAC: %02x:%02x:%02x:%02x:%02x:%02x\r\n",
               MacAddress[0], MacAddress[1], MacAddress[2],
               MacAddress[3], MacAddress[4], MacAddress[5]);

#if SDR_USE_DHCP
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
#else
    xil_printf("Using static network configuration\r\n");
#endif

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

    CtrlPcb = udp_new();
    if (CtrlPcb == NULL) {
        xil_printf("control udp_new failed\r\n");
        udp_remove(UdpPcb);
        UdpPcb = NULL;
        return XST_FAILURE;
    }

    Err = udp_bind(CtrlPcb, IP_ADDR_ANY, SDR_CTRL_UDP_PORT);
    if (Err != ERR_OK) {
        xil_printf("control udp_bind failed: %d\r\n", (int)Err);
        udp_remove(CtrlPcb);
        CtrlPcb = NULL;
        udp_remove(UdpPcb);
        UdpPcb = NULL;
        return XST_FAILURE;
    }

    udp_recv(CtrlPcb, ControlUdpRecv, NULL);
    return XST_SUCCESS;
}

static int SetupRxBd(XAxiDma_Bd *BdPtr, uint8_t *Buffer)
{
    int Status;

    Xil_DCacheFlushRange((UINTPTR)Buffer, SDR_PACKET_BYTES);

    Status = XAxiDma_BdSetBufAddr(BdPtr, (UINTPTR)Buffer);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD set buffer failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = XAxiDma_BdSetLength(BdPtr, SDR_PACKET_BYTES,
                                 RxRing->MaxTransferLen);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD set length failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    XAxiDma_BdSetCtrl(BdPtr, 0U);
    XAxiDma_BdSetId(BdPtr, (UINTPTR)Buffer);
    return XST_SUCCESS;
}

static int PollRxPacket(uint8_t **Data, u32 *Length, XAxiDma_Bd **DoneBd)
{
    XAxiDma_Bd *BdPtr;
    int BdCount;
    u32 DmaStatus;
    u32 BdStatus;

    DmaStatus = XAxiDma_ReadReg(AxiDma.RegBase + XAXIDMA_RX_OFFSET,
                                XAXIDMA_SR_OFFSET);
    if ((DmaStatus & XAXIDMA_ERR_ALL_MASK) != 0U) {
        xil_printf("S2MM SG error, DMASR=0x%08x\r\n",
                   (unsigned int)DmaStatus);
        return -1;
    }

    BdCount = XAxiDma_BdRingFromHw(RxRing, 1, &BdPtr);
    if (BdCount == 0) {
        return 0;
    }

    BdStatus = XAxiDma_BdGetSts(BdPtr);
    if ((BdStatus & XAXIDMA_BD_STS_ALL_ERR_MASK) != 0U) {
        xil_printf("RX BD error, status=0x%08x\r\n",
                   (unsigned int)BdStatus);
        return -1;
    }

    *Data = (uint8_t *)(UINTPTR)XAxiDma_BdGetId(BdPtr);
    *Length = XAxiDma_BdGetActualLength(BdPtr, RxRing->MaxTransferLen);
    *DoneBd = BdPtr;

    Xil_DCacheInvalidateRange((UINTPTR)(*Data), SDR_PACKET_BYTES);
    return 1;
}

static int RequeueRxBd(XAxiDma_Bd *DoneBd, uint8_t *Buffer)
{
    XAxiDma_Bd *BdPtr;
    int Status;

    Status = XAxiDma_BdRingFree(RxRing, 1, DoneBd);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD free failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = XAxiDma_BdRingAlloc(RxRing, 1, &BdPtr);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD alloc failed: %d\r\n", Status);
        return XST_FAILURE;
    }

    Status = SetupRxBd(BdPtr, Buffer);
    if (Status != XST_SUCCESS) {
        return XST_FAILURE;
    }

    Status = XAxiDma_BdRingToHw(RxRing, 1, BdPtr);
    if (Status != XST_SUCCESS) {
        xil_printf("RX BD to hw failed: %d\r\n", Status);
        return XST_FAILURE;
    }

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

static void ConfigureDdc(uint64_t FreqHz)
{
    uint32_t PhaseInc = CalcDdsPhaseInc(FreqHz);
    DdcCenterFreqHz = FreqHz;

    xil_printf("DDC center: %u Hz, PINC=0x%08x\r\n",
               (unsigned int)DdcCenterFreqHz,
               (unsigned int)PhaseInc);

    if (DDC_CTRL_BASEADDR == 0U) {
        xil_printf("DDC AXI-Lite base not present in xparameters yet\r\n");
        return;
    }

    Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_PHASE_REG, PhaseInc);
    xil_printf("DDC AXI-Lite base: 0x%08x version=0x%08x readback=0x%08x\r\n",
               (unsigned int)DDC_CTRL_BASEADDR,
               (unsigned int)Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_VERSION_REG),
               (unsigned int)Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_PHASE_REG));
    Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG, 1U);
    PrintDdcStatus("init");
}

static void SetDdcFrequency(uint64_t FreqHz, const char *Tag)
{
    uint32_t PhaseInc;

    if (FreqHz >= SDR_ADC_CLK_HZ) {
        xil_printf("%s tune rejected: %u Hz outside DDS range\r\n",
                   Tag, (unsigned int)FreqHz);
        return;
    }

    PhaseInc = CalcDdsPhaseInc(FreqHz);
    DdcCenterFreqHz = FreqHz;
    Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_PHASE_REG, PhaseInc);

    xil_printf("%s DDC center=%u Hz PINC=0x%08x readback=0x%08x\r\n",
               Tag,
               (unsigned int)DdcCenterFreqHz,
               (unsigned int)PhaseInc,
               (unsigned int)Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_PHASE_REG));
}

static uint32_t CalcDdsPhaseInc(uint64_t FreqHz)
{
    uint64_t Numerator = (FreqHz << 32) + (SDR_ADC_CLK_HZ / 2ULL);

    return (uint32_t)(Numerator / SDR_ADC_CLK_HZ);
}

static uint64_t ParseUnsigned(const char *Text, uint32_t Length,
                              uint32_t *Consumed)
{
    uint64_t Value = 0ULL;
    uint32_t Index = 0U;

    while ((Index < Length) &&
           ((Text[Index] == ' ') || (Text[Index] == '\t') ||
            (Text[Index] == '=') || (Text[Index] == ':'))) {
        Index++;
    }

    while ((Index < Length) &&
           (Text[Index] >= '0') && (Text[Index] <= '9')) {
        Value = (Value * 10ULL) + (uint64_t)(Text[Index] - '0');
        Index++;
    }

    *Consumed = Index;
    return Value;
}

static void ControlUdpRecv(void *Arg, struct udp_pcb *Pcb, struct pbuf *Pbuf,
                           const ip_addr_t *Addr, u16_t Port)
{
    char Command[64];
    uint32_t Length;
    uint32_t Consumed = 0U;
    uint64_t FreqHz = 0ULL;

    (void)Arg;
    (void)Pcb;

    if (Pbuf == NULL) {
        return;
    }

    Length = (Pbuf->tot_len < (sizeof(Command) - 1U)) ?
             Pbuf->tot_len : (sizeof(Command) - 1U);
    (void)pbuf_copy_partial(Pbuf, Command, Length, 0U);
    Command[Length] = '\0';
    for (uint32_t Index = 0U; Index < Length; Index++) {
        if ((Command[Index] >= 'a') && (Command[Index] <= 'z')) {
            Command[Index] = (char)(Command[Index] - ('a' - 'A'));
        }
    }

    if ((Length >= 4U) &&
        (Command[0] == 'F') && (Command[1] == 'R') &&
        (Command[2] == 'E') && (Command[3] == 'Q')) {
        FreqHz = ParseUnsigned(&Command[4], Length - 4U, &Consumed);
    } else if ((Length >= 6U) &&
               (Command[0] == 'C') && (Command[1] == 'E') &&
               (Command[2] == 'N') && (Command[3] == 'T') &&
               (Command[4] == 'E') && (Command[5] == 'R')) {
        FreqHz = ParseUnsigned(&Command[6], Length - 6U, &Consumed);
    }

    if ((FreqHz > 0ULL) && (FreqHz < SDR_ADC_CLK_HZ)) {
        SetDdcFrequency(FreqHz, "UDP");
        SendControlReply(Addr, Port, "OK\n");
    } else {
        SendControlReply(Addr, Port, "ERR use: FREQ <hz>\n");
    }

    pbuf_free(Pbuf);
}

static void SendControlReply(const ip_addr_t *Addr, u16_t Port,
                             const char *Text)
{
    struct pbuf *Reply;
    uint16_t Length = 0U;

    while (Text[Length] != '\0') {
        Length++;
    }

    Reply = pbuf_alloc(PBUF_TRANSPORT, Length, PBUF_RAM);
    if (Reply == NULL) {
        return;
    }

    if (pbuf_take(Reply, Text, Length) == ERR_OK) {
        (void)udp_sendto(CtrlPcb, Reply, Addr, Port);
    }

    pbuf_free(Reply);
}

static void PrepareSdrUdpPacket(uint32_t Sequence, const uint8_t *Payload)
{
    SdrUdpHeader *Header = (SdrUdpHeader *)TxBuffer;

    Header->Magic = SDR_UDP_MAGIC;
    Header->Sequence = Sequence;
    Header->HeaderBytes = SDR_UDP_HEADER_BYTES;
    Header->SampleCount = SDR_IQ_SAMPLES_PER_PACKET;
    Header->SampleFormat = SDR_SAMPLE_FORMAT_IQ_S16;
    Header->PayloadBytes = SDR_PACKET_BYTES;
    Header->CenterFreqHz = (uint32_t)DdcCenterFreqHz;
    Header->SampleRateHz = (uint32_t)SDR_IQ_SAMPLE_RATE_HZ;

    for (uint32_t Index = 0U; Index < SDR_PACKET_BYTES; Index++) {
        TxBuffer[SDR_UDP_HEADER_BYTES + Index] = Payload[Index];
    }
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

static void PrintDdcStatus(const char *Tag)
{
    u32 Status;
    u32 Counters;
    u32 FifoLevel;
    u32 MaxFifoLevel;
    u32 PacketCount;
    u32 ClipCount;
    u32 DebugFlags;

    if (DDC_CTRL_BASEADDR == 0U) {
        return;
    }

    Status = Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG);
    Counters = Xil_In32(DDC_CTRL_BASEADDR + SDR_DDC_COUNTER_REG);
    FifoLevel = (Status >> SDR_DDC_STATUS_FIFO_SHIFT) &
                SDR_DDC_STATUS_FIFO_MASK;
    MaxFifoLevel = (Status >> SDR_DDC_STATUS_MAX_FIFO_SHIFT) &
                   SDR_DDC_STATUS_FIFO_MASK;
    PacketCount = Counters & 0xffffU;
    ClipCount = (Counters >> 16) & 0xffU;
    DebugFlags = Counters >> 24;

    xil_printf("[%s] DDC status=0x%08x pack=%u maxpack=%u pack_busy=%u fir_block_seen=%u stall_seen=%u clip_seen=%u packets=%u clips=%u dbg=0x%02x ci_halt=%u cq_halt=%u ci_bp=%u fir_bp=%u iq_mis=%u nofifo=%u axis_stall=%u pack_block=%u\r\n",
               Tag,
               (unsigned int)Status,
               (unsigned int)FifoLevel,
               (unsigned int)MaxFifoLevel,
               (unsigned int)(Status & 0x1U),
               (unsigned int)((Status >> 1) & 0x1U),
               (unsigned int)((Status >> 2) & 0x1U),
               (unsigned int)((Status >> 3) & 0x1U),
               (unsigned int)PacketCount,
               (unsigned int)ClipCount,
               (unsigned int)DebugFlags,
               (unsigned int)(DebugFlags & 0x1U),
               (unsigned int)((DebugFlags >> 1) & 0x1U),
               (unsigned int)((DebugFlags >> 2) & 0x1U),
               (unsigned int)((DebugFlags >> 3) & 0x1U),
               (unsigned int)((DebugFlags >> 4) & 0x1U),
               (unsigned int)((DebugFlags >> 5) & 0x1U),
               (unsigned int)((DebugFlags >> 6) & 0x1U),
               (unsigned int)((DebugFlags >> 7) & 0x1U));
}

static int DrainStartupDma(void)
{
    uint32_t Drained = 0U;
    uint32_t BadLength = 0U;
    uint32_t IdleLoops = 0U;

    xil_printf("Draining startup DMA backlog...\r\n");

    while ((Drained < SDR_STARTUP_DRAIN_PACKETS) &&
           (IdleLoops < SDR_STARTUP_DRAIN_IDLE_LOOPS)) {
        uint8_t *PacketData;
        XAxiDma_Bd *DoneBd;
        u32 PacketLength;
        int Status;

        PollNetwork();

        Status = PollRxPacket(&PacketData, &PacketLength, &DoneBd);
        if (Status < 0) {
            PrintS2mmStatus("startup-drain");
            return XST_FAILURE;
        }

        if (Status == 0) {
            IdleLoops++;
            usleep(DMA_POLL_IDLE_US);
            continue;
        }

        IdleLoops = 0U;
        Drained++;
        if (PacketLength != SDR_PACKET_BYTES) {
            BadLength++;
        }

        Status = RequeueRxBd(DoneBd, PacketData);
        if (Status != XST_SUCCESS) {
            PrintS2mmStatus("startup-requeue");
            return XST_FAILURE;
        }
    }

    Xil_Out32(DDC_CTRL_BASEADDR + SDR_DDC_STATUS_REG, 1U);
    usleep(100U);

    xil_printf("Startup drain: packets=%u bad_len=%u idle_loops=%u\r\n",
               (unsigned int)Drained,
               (unsigned int)BadLength,
               (unsigned int)IdleLoops);
    PrintDdcStatus("startup-drain");

    return XST_SUCCESS;
}

#if SDR_RUNTIME_PRINTS
static void GetPacketStats(const uint8_t *Data, int *Min, int *Max, int *Avg,
                           uint32_t *Changed)
{
    const int16_t *Samples = (const int16_t *)Data;
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
#endif
