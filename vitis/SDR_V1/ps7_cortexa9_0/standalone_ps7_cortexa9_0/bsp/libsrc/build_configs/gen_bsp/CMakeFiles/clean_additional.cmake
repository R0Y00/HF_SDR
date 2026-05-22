# Additional clean files
cmake_minimum_required(VERSION 3.16)

if("${CONFIG}" STREQUAL "" OR "${CONFIG}" STREQUAL "")
  file(REMOVE_RECURSE
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\lwipopts.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\sleep.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\xemac_ieee_reg.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\xemacpsif_hw.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\xiltimer.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\xlwipconfig.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\include\\xtimer_config.h"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\lib\\liblwip220.a"
  "D:\\vivadoproject\\HF_SDR\\vitis\\SDR_V1\\ps7_cortexa9_0\\standalone_ps7_cortexa9_0\\bsp\\lib\\libxiltimer.a"
  )
endif()
