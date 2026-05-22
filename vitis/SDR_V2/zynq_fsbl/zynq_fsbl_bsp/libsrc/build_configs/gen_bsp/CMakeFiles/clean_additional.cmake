# Additional clean files
cmake_minimum_required(VERSION 3.16)

if("${CONFIG}" STREQUAL "" OR "${CONFIG}" STREQUAL "")
  file(REMOVE_RECURSE
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\diskio.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\ff.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\ffconf.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\sleep.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\xilffs.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\xilffs_config.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\xilrsa.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\xiltimer.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\include\\xtimer_config.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\lib\\libxilffs.a"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\lib\\libxilrsa.a"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V2\\zynq_fsbl\\zynq_fsbl_bsp\\lib\\libxiltimer.a"
  )
endif()
