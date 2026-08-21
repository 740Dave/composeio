# Grove / STEMMA-QT / Qwiic Peripheral Cross-Section

**Purpose.** Ground the declarative hardware-abstraction (HAL) spec for ComposeIO. These are the
peripherals that will actually plug into the PCB now being designed: a minimal MCU (CH32V* or
ESP32*) running ComposeIO firmware, exposing **Grove** ports (4-pin, 2.0 mm) plus **STEMMA-QT /
Qwiic** ports (JST-SH, 1.0 mm) for I2C. Each peripheral becomes a bus peer: SENSORs *publish*
readings to a semantic namespace; ACTUATORs *subscribe* to a namespace and drive an output.

Organized **primarily by interface** (this drives the HAL taxonomy), then by category. This is a
representative cross-section (~60 parts), not the full ~400-SKU Grove catalog — every interface and
category is covered, not every SKU.

## Source provenance & confidence

| Tag | Meaning |
|-----|---------|
| `[addr-wiki]` | I2C address confirmed from Seeed wiki *I2C And I2C Address of Seeed Product* table |
| `[intro-wiki]` | Interface/category confirmed from Seeed wiki *Grove Entry-level Sensors Guide* |
| `[acc-wiki]` | Interface confirmed from Seeed wiki *Grove Accessories Guide* |
| `[web]` | Confirmed via live web search (address list / product page) |
| `(unverified)` | From Grove-ecosystem knowledge; **not** confirmed against a live source this session |
| `addr: ?` | I2C address not confirmed — deliberately left unknown rather than invented |

**The Seeed storefront (`seeedstudio.com/catalogsearch`) is JS-rendered and did not yield scrapeable
product rows.** All live data below came from the **Seeed wiki** (much more scrapeable — it lists
interface + I2C addresses), plus targeted web searches and cross-checks against the Adafruit /
i2cdevices.org address lists. Anything I could not pin to one of those is marked `(unverified)` or
`addr: ?`.

Live sources fetched/searched this session:
- Seeed wiki — I2C And I2C Address of Seeed Product: https://wiki.seeedstudio.com/I2C_And_I2C_Address_of_Seeed_Product/
- Seeed wiki — Grove Entry-level Sensors Guide: https://wiki.seeedstudio.com/Grove_Sensor_Intro/
- Seeed wiki — Grove Accessories Guide: https://wiki.seeedstudio.com/Grove_Accessories_Intro/
- Seeed wiki — Grove System (connector pinout): https://wiki.seeedstudio.com/Grove_System/
- Seeed wiki — Grove GPS (Air530): https://wiki.seeedstudio.com/Grove-GPS-Air530/
- Seeed wiki — Grove NFC (PN532): https://wiki.seeedstudio.com/Grove_NFC/
- Adafruit STEMMA-QT / Qwiic compatibility: https://learn.adafruit.com/introducing-adafruit-stemma-qt/technical-specs
- I2C address references: https://learn.adafruit.com/i2c-addresses/the-list , https://i2cdevices.org/addresses

---

## 1. Interface-summary table (top-level HAL kinds)

| Interface | Grove pins used | Connector(s) | Topology | Cross-compat note |
|-----------|-----------------|--------------|----------|-------------------|
| **I2C** | 4 (SCL=pin1, SDA=pin2, VCC, GND) | Grove-I2C **and** STEMMA-QT / Qwiic (JST-SH 1.0 mm) | **Bus-shared** — many devices on one bus, disambiguated by 7-bit address | Grove-I2C ≡ STEMMA-QT ≡ Qwiic *electrically* (same SDA/SCL/VCC/GND). Physically Grove is 2.0 mm 4-pin; QT/Qwiic is JST-SH 1.0 mm. Adapter cables just re-map pin order. One I2C port can fan out to a whole chain. |
| **UART** | 4 (RX=pin1, TX=pin2, VCC, GND) | Grove-UART (4-pin 2.0 mm only) | **Point-to-point** — one device per port (unless a soft-mux) | Grove-specific. Not on QT/Qwiic. |
| **Analog (ADC)** | 4 (A0=pin1, A1=pin2 optional, VCC, GND) | Grove-Analog (4-pin 2.0 mm only) | **Point-to-point** — one sensor per ADC channel | Grove-specific. Needs an ADC pin on the MCU. |
| **Digital-in (GPIO)** | 4 (D0=pin1, D1=pin2 optional, VCC, GND) | Grove-Digital (4-pin 2.0 mm only) | **Point-to-point** — one per port | Grove-specific. |
| **Digital/PWM-out** | 4 (D0=pin1, VCC, GND) | Grove-Digital (4-pin 2.0 mm only) | **Point-to-point** — one actuator per port | Grove-specific. PWM needs a timer-capable pin. |
| **1-Wire / single-wire** | 4 (signal=pin1, VCC, GND) | Grove-Digital (4-pin 2.0 mm only) | 1-Wire = **bus-shared** (multi-drop by 64-bit ROM ID); DHT single-wire = point-to-point | Grove-specific. DS18B20 1-Wire ≠ DHT single-wire protocol. |
| **SPI** | 4-pin Grove can't carry full SPI; Grove-SPI uses the base-shield SPI header, not a Grove port | header / QT rare | **Bus-shared** (chip-select per device) | Few native Grove-SPI parts; most "SPI" Grove sensors are dual-mode I2C/SPI and are wired as I2C on a 4-pin port. |

**Grove connector pinout** (from wiki *Grove System*): pin1 yellow, pin2 white, pin3 red (VCC),
pin4 black (GND). The *role* of pins 1/2 is what distinguishes the four Grove port types
(SCL/SDA vs RX/TX vs A0/A1 vs D0/D1). `[intro-wiki]`

**Direction convention:** SENSOR = input → firmware **publishes** to namespace.
ACTUATOR = output ← firmware **subscribes** to namespace and drives the pin/register.

---

## 2. I2C peripherals (the big one — bus-shared, address-multiplexed)

Works identically on a Grove-I2C port or a STEMMA-QT / Qwiic port. Address is the disambiguator, so
this is the interface where the HAL most needs an `addr` + `driver` pair.

### 2a. Environmental (temp / humidity / pressure / gas / CO2)

| Name | Category | I2C addr | Dir | Measures | Semantic namespace(s) | Notes / driver |
|------|----------|----------|-----|----------|-----------------------|----------------|
| Grove BME280 | env T/H/P | 0x76 / 0x77 `[web]` | SENSOR | temp, humidity, pressure | env/temperature, env/humidity, env/pressure | classic 3-in-1 |
| Grove BMP280 | env P/T | 0x76 / 0x77 (unverified) | SENSOR | pressure, temp | env/pressure, env/temperature | pressure+temp only |
| Grove BME680 | env + gas | 0x76 def / 0x77 opt `[addr-wiki]` | SENSOR | temp, humidity, pressure, gas (VOC) | env/temperature, env/humidity, env/pressure, air/voc_index | 4-in-1 |
| Grove SHT31 | env T/H | 0x44 `[addr-wiki]` | SENSOR | temp, humidity | env/temperature, env/humidity | |
| Grove SHT35 | env T/H | 0x44 (unverified) | SENSOR | temp, humidity | env/temperature, env/humidity | higher accuracy SHT3x |
| Grove SHT40 (SHT4x) | env T/H | 0x44 (unverified; SHT4x family 0x44–0x46) `[web]` | SENSOR | temp, humidity | env/temperature, env/humidity | STEMMA-QT staple |
| Grove AHT20 / DHT20 | env T/H | 0x38 `[web]` | SENSOR | temp, humidity | env/temperature, env/humidity | |
| Grove TH02 | env T/H | addr: ? | SENSOR | temp, humidity | env/temperature, env/humidity | `[intro-wiki]` |
| Grove Barometer (BMP180) | env P | 0x77 `[addr-wiki]` | SENSOR | pressure, altitude | env/pressure, env/altitude | |
| Grove DPS310 | env P | addr: ? (unverified 0x77) | SENSOR | pressure | env/pressure | high-precision |
| Grove SGP30 (VOC/eCO2) | air quality | 0x58 `[addr-wiki]` | SENSOR | TVOC, eCO2 | air/tvoc, air/eco2 | gas-phase, not true CO2 |
| Grove SGP40 / SGP41 | air quality | addr: ? (unverified 0x59) | SENSOR | VOC index (+NOx on 41) | air/voc_index, air/nox_index | |
| Grove SCD41 (SCD4x) | true CO2 | 0x62 (unverified — Sensirion default) | SENSOR | CO2, temp, humidity | air/co2, env/temperature, env/humidity | photoacoustic NDIR |
| Grove SCD30 | true CO2 | 0x61 (unverified — Sensirion default) | SENSOR | CO2, temp, humidity | air/co2, env/temperature, env/humidity | |
| Grove Multichannel Gas v2 | gas | 0x08 (unverified; v1 = 0x04 `[addr-wiki]`) | SENSOR | CO, NO2, C2H5OH, VOC | air/gas/{co,no2,ethanol,voc} | 4-ch MEMS |
| Grove HM3301 Laser PM2.5 | dust | addr: ? (unverified 0x40) | SENSOR | PM1.0/PM2.5/PM10 | air/pm/{1_0,2_5,10} | `[intro-wiki]` |
| Grove SFA30 | formaldehyde | addr: ? (unverified 0x5D) | SENSOR | HCHO, temp, humidity | air/hcho, env/temperature, env/humidity | |

### 2b. Light / color / UV / gesture

| Name | Category | I2C addr | Dir | Measures | Semantic namespace(s) | Notes |
|------|----------|----------|-----|----------|-----------------------|-------|
| Grove Digital Light (TSL2561) | light | 0x29 `[addr-wiki]` | SENSOR | lux (IR+visible) | light/lux | `[intro-wiki]` |
| VEML7700 (STEMMA-QT) | light | 0x10 `[web]` | SENSOR | lux (ambient) | light/lux | Adafruit/Seeed QT |
| TSL2591 (STEMMA-QT) | light | 0x29 `[web]` | SENSOR | lux, high-dynamic-range | light/lux | |
| Grove Sunlight Sensor (SI1145-class) | light/UV | 0x45 `[addr-wiki]` | SENSOR | visible, IR, UV index | light/lux, light/uv_index | |
| Grove I2C UV (VEML6070) | UV | 0x38 LSB / 0x39 MSB `[addr-wiki]` | SENSOR | UV intensity | light/uv_index | |
| APDS9960 (STEMMA-QT / Grove) | gesture/color/prox | 0x39 (unverified) | SENSOR | gesture, RGB color, proximity | gesture/event, light/color, distance/proximity | |
| Grove Gesture (PAJ7620U2) | gesture | 0x73 `[addr-wiki]` | SENSOR | 9-gesture recognition | gesture/event | |
| Grove I2C Color Sensor v2 (TCS3472) | color | 0x29 `[addr-wiki]` | SENSOR | RGB + clear | light/color | |

### 2c. Motion / IMU / orientation

| Name | Category | I2C addr | Dir | Measures | Semantic namespace(s) | Notes |
|------|----------|----------|-----|----------|-----------------------|-------|
| Grove 3-Axis Digital Gyro (MPU6050-class) | IMU | 0x68 `[addr-wiki]` | SENSOR | gyro x/y/z (+accel) | motion/gyro/{x,y,z}, motion/accel/{x,y,z} | MPU6050 = 0x68/0x69 (unverified) |
| MPU6050 (STEMMA-QT / Grove) | IMU 6-axis | 0x68 def / 0x69 `[addr-wiki via gyro]` | SENSOR | accel + gyro | motion/accel/{x,y,z}, motion/gyro/{x,y,z} | |
| Grove IMU 9DOF v2 (MPU9250) | IMU 9-axis | 0x68 / 0x69 `[addr-wiki]` | SENSOR | accel, gyro, mag | motion/accel/*, motion/gyro/*, motion/mag/* | |
| Grove IMU 10DOF v2 | IMU 10-axis | 0x68 / 0x69 `[addr-wiki]` | SENSOR | accel, gyro, mag, baro | motion/*, env/pressure | |
| LSM6DS3 (STEMMA-QT) | IMU 6-axis | 0x6A / 0x6B (unverified) | SENSOR | accel + gyro | motion/accel/*, motion/gyro/* | |
| Grove 3-Axis Accel ±16g (ADXL345) | accel | 0x53 `[addr-wiki]` | SENSOR | accel x/y/z | motion/accel/{x,y,z} | ADXL345 also 0x1D `[web]` |
| Grove 3-Axis Accel LIS3DHTR | accel | addr: ? (unverified 0x19) | SENSOR | accel x/y/z | motion/accel/{x,y,z} | |
| Grove BMA400 ±16g | accel | 0x15 def / 0x14 opt `[addr-wiki]` | SENSOR | accel (ultra-low-power) | motion/accel/{x,y,z} | |
| Grove 3-Axis Digital Compass (HMC5883L-class) | magnetometer | 0x1E `[addr-wiki]` | SENSOR | magnetic field x/y/z | motion/mag/{x,y,z}, motion/heading | |
| Grove 6-Axis Accel&Compass v2 | accel+mag | 0x1E `[addr-wiki]` | SENSOR | accel, magnetic | motion/accel/*, motion/mag/* | |
| Grove BMI088 6-Axis | IMU 6-axis | 0x19 def / 0x18 opt `[addr-wiki]` | SENSOR | accel + gyro | motion/accel/*, motion/gyro/* | |
| Grove Step Counter (BMA456) | pedometer | 0x19 def / 0x18 opt `[addr-wiki]` | SENSOR | steps, motion | motion/steps | |
| Grove AS5600 Magnetic Encoder | angle | 0x36 (unverified) | SENSOR | 12-bit absolute angle | rotation/angle | |

### 2d. Distance / proximity / ToF

| Name | Category | I2C addr | Dir | Measures | Semantic namespace(s) | Notes |
|------|----------|----------|-----|----------|-----------------------|-------|
| Grove ToF Distance (VL53L0X) | ToF distance | 0x29 `[addr-wiki]` | SENSOR | distance mm | distance/mm | addr software-settable `[web]` |
| VL53L1X (STEMMA-QT) | ToF distance | 0x29 (unverified) | SENSOR | distance mm (longer range) | distance/mm | |
| Grove AMG8833 IR Array | thermal cam | 0x68 def / 0x69 opt `[addr-wiki]` | SENSOR | 8×8 temp grid | thermal/grid | |

### 2e. Displays (I2C output actuators)

| Name | Category | I2C addr | Dir | Controls | Semantic namespace(s) | Notes |
|------|----------|----------|-----|----------|-----------------------|-------|
| Grove OLED 0.96" (SSD1306) | display | 0x3C `[addr-wiki]` | ACTUATOR | monochrome framebuffer | display/text, display/framebuffer | |
| Grove OLED 1.12" (SH1107) | display | 0x3C `[addr-wiki]` | ACTUATOR | monochrome framebuffer | display/text, display/framebuffer | |
| Grove LCD RGB Backlight | display | 0x3E (LCD) / 0x62 (RGB) `[addr-wiki]` | ACTUATOR | 16×2 text + RGB backlight | display/text, led/color | dual-address device |
| Grove 16×2 LCD | display | 0x3E `[addr-wiki]` | ACTUATOR | 16×2 text | display/text | |
| Grove LED Matrix (HT16K33) | display | addr: ? (unverified 0x70) | ACTUATOR | LED matrix | display/framebuffer | |

### 2f. RTC / ADC / expanders / touch / drivers (I2C)

| Name | Category | I2C addr | Dir | Measures/Controls | Semantic namespace(s) | Notes / driver |
|------|----------|----------|-----|-------------------|-----------------------|----------------|
| Grove RTC (DS1307) | clock | 0x68 `[addr-wiki]` | SENSOR | wall-clock time | time/rtc | |
| Grove High Precision RTC (DS3231-class) | clock | 0x51 `[addr-wiki]` | SENSOR | time + temp | time/rtc, env/temperature | |
| Grove I2C ADC (ADC121C021) | ADC expander | 0x55 `[addr-wiki]` | SENSOR | 12-bit analog→I2C | analog/raw | bridges an analog sensor onto the bus |
| Grove 12-Key Cap Touch (MPR121) | touch | 0x5B def / 0x5C / 0x5D `[addr-wiki]` | SENSOR | 12 touch channels | touch/key/{0..11} | |
| Grove I2C Touch (MPR121) | touch | 0x5A–0x5D `[addr-wiki]` | SENSOR | touch state | touch/key/* | |
| Grove 16-Ch PWM Driver (PCA9685) | servo/PWM driver | 0x40 (unverified; range 0x40–0x7F) | ACTUATOR | 16× PWM/servo channels | servo/angle, pwm/duty | maps to servo/LED banks |
| Grove I2C Motor Driver | motor driver | 0x0F `[addr-wiki]` | ACTUATOR | dual DC motor | motor/speed, motor/direction | |
| Grove 4-Ch SPDT Relay (I2C) | relay bank | 0x11 / 0x12 `[addr-wiki]` | ACTUATOR | 4 relays | relay/{0..3}/state | |
| Grove Haptic Motor (DRV2605) | haptics | 0x5A `[addr-wiki]` | ACTUATOR | haptic waveform | haptic/effect | |
| Grove Finger-clip Heart Rate | biometric | 0xA0 `[addr-wiki]` | SENSOR | BPM | health/heart_rate | 0xA0 = 8-bit form of 0x50 |

---

## 3. UART peripherals (point-to-point, one per port)

Distinguished by **baud + line protocol/driver**, not by an address. Firmware needs a per-port
software UART or a hardware UART.

| Name | Category | Baud | Dir | Measures/Controls | Semantic namespace(s) | Driver / notes |
|------|----------|------|-----|-------------------|-----------------------|----------------|
| Grove GPS (Air530 / Air530Z) | GNSS | 9600 `[web]` | SENSOR | lat, lon, alt, fix, time | geo/position/{lat,lon,alt}, geo/fix, time/gps | NMEA-0183 `[web]` |
| Grove GPS (SIM28 / older) | GNSS | 9600 (unverified) | SENSOR | lat, lon, alt | geo/position/* | NMEA |
| Grove NFC (PN532) — UART mode | NFC/RFID | 115200 (unverified; UART default) | SENSOR/ACTUATOR | tag UID, read/write blocks | nfc/tag_uid, nfc/read, nfc/write | **maps to built driver `composeio.peripherals.nfc`** (PN532). Also I2C addr 0x24 `[addr-wiki]` |
| GROW R503 Fingerprint | biometric | 57600 (unverified) | SENSOR | fingerprint match/enroll | biometric/fingerprint/{match,enroll,id} | capacitive; RGB ring is an actuator sub-fn |
| Grove MH-Z16 CO2 (NDIR) | true CO2 | 9600 `[intro-wiki]` | SENSOR | CO2 ppm | air/co2 | true NDIR CO2 |
| Grove TF-Mini LiDAR | distance | 115200 (unverified) | SENSOR | distance | distance/mm | `[intro-wiki]` (UART) |
| Grove 125 kHz RFID Reader | RFID | 9600 (unverified) | SENSOR | card UID | rfid/tag_uid | Wiegand/UART variants exist |
| Grove Serial MP3 / DFPlayer-class | audio out | 9600 (unverified) | ACTUATOR | track playback | audio/play, audio/track | **maps to audio backend** |

---

## 4. Analog (ADC) peripherals (point-to-point, one per ADC channel)

Firmware reads a raw ADC count on pin A0; the HAL needs a `scale`/`map` to convert to the physical
quantity. All are SENSORs.

| Name | Category | Dir | Measures | Semantic namespace(s) | Notes |
|------|----------|-----|----------|-----------------------|-------|
| Grove Rotary Angle (10 kΩ pot, 300°) | input | SENSOR | angle 0–300° | input/rotary, rotation/angle | `[web]` analog 0–Vcc |
| Grove Slide Potentiometer (10 kΩ) | input | SENSOR | linear position | input/slider, position/linear | `[intro-wiki]` (has LED = actuator sub-fn) |
| Grove Light Sensor (LDR) | light | SENSOR | light intensity (relative) | light/level | `[web]` not calibrated lux |
| Grove Sound Sensor | sound | SENSOR | sound level (instantaneous) | audio/level | `[intro-wiki]` |
| Grove Loudness Sensor | sound | SENSOR | envelope loudness | audio/loudness | `[web]` LM2904 + mic |
| Grove Analog Microphone (MEMS) | sound | SENSOR | audio waveform | audio/waveform | |
| Grove Moisture (resistive) | soil | SENSOR | soil moisture | soil/moisture | `[intro-wiki]` |
| Grove Capacitive Moisture | soil | SENSOR | soil moisture | soil/moisture | corrosion-resistant |
| Grove Water Level | liquid | SENSOR | water level (≤10 cm) | liquid/level | `[intro-wiki]` |
| Grove TDS Sensor | liquid | SENSOR | dissolved solids | liquid/tds | |
| Grove GSR Sensor | biometric | SENSOR | skin conductance | biometric/gsr | |
| Grove ADXL335 3-Axis Accel | accel (analog) | SENSOR | accel x/y/z (3 ADC pins) | motion/accel/{x,y,z} | analog IMU variant |
| Grove GUVA-S12D UV | UV | SENSOR | UV intensity | light/uv_index | |
| Grove Gas MQ-x (MQ2/3/5/9) | gas | SENSOR | gas concentration (relative) | air/gas/{smoke,alcohol,lpg,co} | heater; ratiometric |
| Grove Piezo Vibration | vibration | SENSOR | vibration/impact | motion/vibration | |
| Grove FSR402 Force | force | SENSOR | pressure/force | input/force | |
| Grove Thermistor / High-Temp | temp (analog) | SENSOR | temperature | env/temperature | needs Steinhart-Hart map |

---

## 5. Digital-in / GPIO peripherals (point-to-point, one per port)

Firmware reads a logic level (optionally with pull + debounce). All SENSORs.

| Name | Category | Dir | Measures | Semantic namespace(s) | Notes |
|------|----------|-----|----------|-----------------------|-------|
| Grove Button | input | SENSOR | pressed/released | input/button, button/pressed | debounce needed |
| Grove Touch Sensor (cap, single) | input | SENSOR | touch on/off | touch/state | `[intro-wiki]` |
| Grove PIR Motion | presence | SENSOR | motion detected | presence/detected, motion/pir | `[intro-wiki]` 3 m range |
| Grove Adjustable PIR / mini PIR | presence | SENSOR | motion detected | presence/detected | |
| Grove Tilt Switch | orientation | SENSOR | tilted/level | orientation/tilt | `[intro-wiki]` |
| Grove Magnetic Switch / Reed | proximity | SENSOR | magnet near/far | contact/reed, door/open | |
| Grove Line Finder | reflectance | SENSOR | line under sensor | line/detected | `[intro-wiki]` |
| Grove Water Sensor | liquid | SENSOR | water present | liquid/present | digital |
| Grove Flame Sensor | fire | SENSOR | flame detected | alarm/flame | `[intro-wiki]` |
| Grove IR Receiver | remote | SENSOR | decoded IR code | ir/code | 38 kHz |
| Grove Ultrasonic Ranger (HC-SR04-class) | distance | SENSOR | distance (pulse timing) | distance/mm | single-pin trig/echo; needs timing driver `[intro-wiki]` |
| Grove Hall Sensor | magnetic | SENSOR | magnetic presence | magnetic/detected | |
| Grove Collision / Vibration (SW-420) | impact | SENSOR | shock event | motion/shock | |

---

## 6. Digital / PWM out — actuators (point-to-point, one per port)

Firmware **subscribes** to the namespace and drives the pin. `state`-style = digital out;
`angle`/`tone`/`speed` = PWM.

| Name | Category | Interface | Dir | Controls | Semantic namespace(s) | Driver / notes |
|------|----------|-----------|-----|----------|-----------------------|----------------|
| Grove Relay (1-ch) | switch | Digital-out | ACTUATOR | mains switch | relay/state | `[acc-wiki]` |
| Grove 2-Ch SPDT Relay | switch | Digital-out | ACTUATOR | 2 relays | relay/{0,1}/state | `[acc-wiki]` |
| Grove Solid State Relay | switch | Digital-out | ACTUATOR | AC load | relay/state | |
| Grove Buzzer (active) | audio | Digital-out | ACTUATOR | on/off beep | buzzer/state | `[acc-wiki]` |
| Grove Passive Buzzer | audio | PWM-out | ACTUATOR | tone frequency | buzzer/tone | `[acc-wiki]` PWM → **audio backend** for tones |
| Grove Servo | motion | PWM-out | ACTUATOR | angle 0–180° | servo/angle | `[acc-wiki]` |
| Grove Vibration Motor | haptics | Digital-out | ACTUATOR | vibrate on/off | haptic/state | `[acc-wiki]` |
| Grove Mini Fan | thermal | Digital-out | ACTUATOR | fan on/off | fan/state | `[acc-wiki]` |
| Grove Red LED | indicator | Digital/PWM-out | ACTUATOR | brightness | led/state, led/brightness | `[acc-wiki]` |
| Grove LED (single, various) | indicator | Digital/PWM-out | ACTUATOR | on/off / brightness | led/state | |
| Grove Water Atomization | actuator | Digital-out | ACTUATOR | mist on/off | atomizer/state | `[acc-wiki]` |
| Grove Infrared Emitter | remote | Digital-out | ACTUATOR | IR blast | ir/emit | `[acc-wiki]` |
| Grove Speaker | audio | Analog-out | ACTUATOR | tone / audio | audio/out | `[acc-wiki]` → **audio backend / DAC** |

---

## 7. 1-Wire / single-wire / addressable-LED (special protocols)

| Name | Category | Interface | Dir | Measures/Controls | Semantic namespace(s) | Driver / notes |
|------|----------|-----------|-----|-------------------|-----------------------|----------------|
| DS18B20 (waterproof) | temp | **1-Wire** (multi-drop by 64-bit ROM ID) | SENSOR | temperature | env/temperature | bus-shared: many probes one pin `[intro-wiki]` |
| Grove MAX31850K Thermocouple | temp | **1-Wire** | SENSOR | temperature (K-type) | env/temperature | `[intro-wiki]` |
| Grove DHT11 | env T/H | **single-wire** (proprietary, ≠ 1-Wire) | SENSOR | temp, humidity | env/temperature, env/humidity | `[intro-wiki]` slow poll |
| Grove DHT22 / AM2302 | env T/H | **single-wire** | SENSOR | temp, humidity | env/temperature, env/humidity | `[intro-wiki]` |
| Grove Chainable RGB LED (P9813) | LED | 2-wire clock+data | ACTUATOR | per-LED RGB | led/color | `[acc-wiki]` |
| Grove RGB LED Stick / Ring (WS2813) | LED | **WS281x 1-wire timed** | ACTUATOR | addressable RGB strip | led/color, led/pixel/{i}/color | `[acc-wiki]` **maps to WLED adaptor family** |
| Grove LED Strip Driver (WS2812/analog strip) | LED | timed / analog | ACTUATOR | strip color | led/color | `[acc-wiki]` **maps to WLED adaptor family** |
| Grove HX711 Load Cell | weight | 2-wire (clock+data, bit-banged) | SENSOR | weight / force | weight/grams | `[intro-wiki]` 24-bit |

---

## 8. SPI peripherals (bus-shared, chip-select per device)

Few Grove parts are *natively* SPI over a 4-pin port — a standard 4-pin Grove can't carry
SCK+MOSI+MISO+CS. Most "I2C/SPI" dual-mode Grove sensors (BMP280, ADXL345, BMI088, BMM150, LSM6DS3,
AS3935, ADXL345, PAJ7660) are wired as **I2C** on Grove/QT ports; SPI mode needs the base-shield SPI
header. Treat SPI as a HAL kind for completeness, but on this PCB most of these arrive as I2C.

| Name | Category | Interface | Dir | Measures | Semantic namespace | Notes |
|------|----------|-----------|-----|----------|--------------------|-------|
| Grove MP3 v4 | audio | I2C/SPI `[acc-wiki]` | ACTUATOR | playback | audio/play | dual-mode → **audio backend** |
| Grove AS3935 Lightning | light/EM | I2C/SPI (unverified) | SENSOR | lightning distance | alarm/lightning | `[intro-wiki]` |
| Grove ADXL345 / BMI088 / BMM150 | IMU | I2C/SPI | SENSOR | motion | motion/* | usually wired I2C (see §2c) |

---

## 9. Which parts map to existing ComposeIO drivers

| Peripheral family | Existing ComposeIO driver / adaptor |
|-------------------|-------------------------------------|
| NFC/RFID (PN532, Grove NFC) | **`composeio.peripherals.nfc`** (built PN532 driver) — UART or I2C@0x24 |
| Addressable RGB LED (WS281x strips/rings/sticks, LED Strip Driver) | **WLED adaptor family** |
| Buzzer (passive/PWM), Speaker, MP3, DAC-out | **audio backend** |
| Generic I2C sensor bank | one I2C `driver` entry per chip, keyed by `addr` (see HAL synthesis) |

---

## 10. What the HAL taxonomy needs (synthesis — the payload for the architect)

To declaratively cover this entire cross-section, the HAL schema must express a small closed set of
**interface KINDS**, each with a handful of parameters. A peripheral is then: *(port → interface-kind
→ params → driver → published/subscribed namespace)*.

**Minimal interface KINDS + per-kind parameters:**

```yaml
# one entry per physically-attached peripheral
peripheral:
  port: <port-id>                 # which physical Grove/QT socket
  role: sensor | actuator         # publish vs subscribe
  namespace: <semantic/name>      # or a map of sub-quantities → namespaces
  interface:                      # exactly one of the KINDS below

    # --- I2C: bus-shared, address-multiplexed (Grove-I2C ≡ STEMMA-QT ≡ Qwiic) ---
    i2c:
      addr: 0x00                  # 7-bit; REQUIRED disambiguator; may be a list for multi-addr parts
      driver: <chip-id>           # e.g. bme280, vl53l0x, ssd1306 — selects register map
      bus: <bus-id>               # a port may share a bus with a whole QT chain
      # optional: alt_addr for address-select straps

    # --- analog: point-to-point, one ADC channel ---
    analog:
      pin: <adc-pin>
      scale: { map: linear|steinhart|ratiometric, in: [min,max], out: [min,max] }
      # raw ADC count → physical quantity; unit carried by namespace

    # --- digital_in: point-to-point GPIO ---
    digital_in:
      pin: <gpio>
      pull: none | up | down
      debounce_ms: <int>
      active: high | low
      edge: rising | falling | both   # for event-style (button, PIR)

    # --- digital_out / pwm_out: point-to-point actuator ---
    pwm_out:
      pin: <gpio>
      mode: digital | pwm
      freq_hz: <int>              # PWM only (servo ~50, buzzer tone, LED dim)
      # servo: angle→pulse-width map; buzzer: value→freq

    # --- uart: point-to-point, protocol-driven ---
    uart:
      baud: <int>                 # 9600 GPS, 57600 R503, 115200 PN532/TFmini
      driver: <proto-id>          # nmea, pn532, r503, mhz16 — frames the byte stream
      # optional: parity, stop, flow

    # --- onewire: bus-shared by 64-bit ROM id (DS18B20, MAX31850) ---
    onewire:
      pin: <gpio>
      driver: ds18b20 | max31850
      rom: <64-bit id>            # optional; selects one probe on a multi-drop bus

    # --- single_wire: DHT proprietary (NOT 1-Wire) ---
    single_wire:
      pin: <gpio>
      driver: dht11 | dht22
      poll_ms: <int>             # slow (>=1–2 s)

    # --- ws281x: timed serial addressable LED (maps to WLED adaptor) ---
    ws281x:
      pin: <gpio>
      count: <int>               # pixel count
      order: grb | rgb | grbw

    # --- spi: bus-shared, chip-select per device (rare on 4-pin Grove) ---
    spi:
      bus: <spi-bus>
      cs: <gpio>
      driver: <chip-id>
      mode: 0..3
      # most dual-mode Grove parts are wired as i2c instead
```

**Key structural facts the schema must encode:**

1. **Bus-shared vs point-to-point is the primary axis.** `i2c`, `onewire`, `spi` are bus-shared
   (multiple devices per bus → each needs a *selector*: I2C `addr`, 1-Wire `rom`, SPI `cs`). `analog`,
   `digital_in`, `pwm_out`, `uart`, `single_wire`, `ws281x` are point-to-point (one device owns the
   port). This is exactly what determines whether one physical socket can host many peripherals.

2. **I2C is where `driver` + `addr` do the heavy lifting.** The same 4 wires and the same connector
   (Grove-I2C, STEMMA-QT, Qwiic are electrically identical) host dozens of different chips; the
   `(addr, driver)` pair is the entire identity. Address collisions on a shared bus are a real
   constraint the schema/validator should catch (e.g. 0x68 is used by DS1307 RTC, MPU6050, AMG8833,
   many IMUs — can't chain two on one bus without an address strap or a mux).

3. **`driver` selects the semantic decode.** For I2C/UART/1-Wire, the byte stream is meaningless
   without a per-chip/per-protocol decoder. The HAL's `driver` field is the join key to a registry
   of decoders (and to existing ComposeIO drivers: `nfc`→PN532, WLED→ws281x, audio→buzzer/DAC).

4. **`role` = publish vs subscribe.** SENSOR → firmware publishes to `namespace`; ACTUATOR →
   firmware subscribes. A few parts are both (PN532 read/write; LCD-RGB has a text actuator + RGB
   actuator; slide-pot has an input + an LED). The schema should allow a peripheral to bind *multiple*
   namespaces (a BME280 publishes 3; an IMU publishes 6–9).

5. **Namespaces are grouped by physical quantity, not by part.** `env/temperature` is published
   identically by BME280, SHT31, DS18B20, a thermistor, or a DS3231 — the HAL decouples the semantic
   name from the silicon. Actuator namespaces mirror this: `relay/state`, `servo/angle`,
   `buzzer/tone`, `led/color`, `motor/speed`.

**Minimal closed set the architect must design for:** `i2c{addr,driver,bus}`,
`analog{pin,scale}`, `digital_in{pin,pull,debounce,edge}`, `pwm_out{pin,mode,freq}`,
`uart{baud,driver}`, `onewire{pin,driver,rom}`, `single_wire{pin,driver}`,
`ws281x{pin,count,order}`, `spi{bus,cs,driver,mode}`. Nine kinds cover 100% of this cross-section.

---

## 11. Coverage notes & gaps

- **Live-sourced:** all I2C addresses tagged `[addr-wiki]`/`[web]`; interface+category for most parts
  `[intro-wiki]`/`[acc-wiki]`; connector cross-compat and Grove pinout; GPS baud; PN532 addr.
- **`(unverified)` (knowledge, not confirmed this session):** SCD41=0x62, SCD30=0x61, LSM6DS3=0x6A/6B,
  APDS9960=0x39, AS5600=0x36, MPU6050=0x68/0x69, BMP280=0x76/0x77, PCA9685=0x40+, several UART baud
  rates (R503 57600, PN532-UART 115200, TF-Mini 115200). These are well-known ecosystem values, but
  verify against each part's datasheet before locking the spec.
- **`addr: ?` (deliberately unknown):** TH02, DPS310, HM3301, SFA30, SGP40/41, LIS3DHTR, HT16K33
  matrix, VL53L1X, Multichannel Gas v2 — confirm from the specific SKU's datasheet.
- **Storefront gap:** `seeedstudio.com/catalogsearch` is JS-rendered and was not scrapeable; all Seeed
  data came from the wiki, which is the more reliable source for interface + address anyway.
- **SPI is thin by design** — Grove's 4-pin ports can't carry full SPI, so most "SPI-capable" Grove
  sensors arrive as I2C on this PCB. The `spi` HAL kind is included for completeness (base-shield
  header, QT SPI variants) but will be lightly used.
- Did not enumerate every gas/liquid/AI-camera SKU (the catalog has 400+); the cross-section covers
  every *interface kind* and every *category*, which is what the HAL taxonomy needs.
