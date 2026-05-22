
cmake_minimum_required(VERSION 3.15)
project(bsp)

# Use CMAKE_LIBRARY_PATH in link_directories
link_directories(${CMAKE_LIBRARY_PATH})
        
set(DRIVER_TARGETS axidma;coresightps_dcc;devcfg;dmaps;emacps;gpiops;scugic;qspips;scutimer;scuwdt;sdps;uartps;usbps;xadcps)
set(DRIVER_LOCATIONS D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/axidma_v9_20;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/coresightps_dcc_v1_13;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/devcfg_v3_9;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/dmaps_v2_11;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/emacps_v3_23;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/gpiops_v3_14;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/scugic_v5_6;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/qspips_v3_14;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/scutimer_v2_7;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/scuwdt_v2_6;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/sdps_v4_5;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/uartps_v3_17;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/usbps_v2_10;D:/Xilinx/2025.2/Vitis/data/embeddedsw/XilinxProcessorIPLib/drivers/xadcps_v2_7)

list(LENGTH DRIVER_TARGETS no_of_drivers)
set(index 0)

while(${index} LESS ${no_of_drivers})
    list(GET DRIVER_TARGETS ${index} drv)
    list(GET DRIVER_LOCATIONS ${index} drv_dir)
    set(exp_dir "${drv_dir}/examples")
    add_custom_target(
        ${drv}_example ALL
        COMMAND lopper -f -O D:/vivadoproject/HF_SDR/vitis/SDR_V1/ps7_cortexa9_0/standalone_ps7_cortexa9_0/bsp/libsrc/${drv} D:/vivadoproject/HF_SDR/vitis/SDR_V1/ps7_cortexa9_0/standalone_ps7_cortexa9_0/bsp/hw_artifacts/ps7_cortexa9_0_baremetal.dts -- bmcmake_metadata_xlnx ps7_cortexa9_0 ${exp_dir} drvcmake_metadata
        BYPRODUCTS x${drv}_exlist.yaml)
    MATH(EXPR index "${index}+1")
endwhile()
