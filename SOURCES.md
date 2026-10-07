# Technical sources used for the pilot

These are the primary external references used to choose the PPK2 framing, timing and local stack. They are not vendored into this repository.

## Nordic PPK2

- Nordic Power Profiler application source: https://github.com/NordicSemiconductor/pc-nrfconnect-ppk
- PPK2 serial decoder (`serialDevice.ts`): https://github.com/NordicSemiconductor/pc-nrfconnect-ppk/blob/main/src/device/serialDevice.ts
- Nordic PPK2 product/documentation: https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2

Relevant implementation facts from Nordic's decoder:

- ADC sampling time: 10 µs
- current/range/counter/logic are encoded in one 32-bit sample word
- 6-bit measurement counter is used to detect data loss
- 8 logic bits D0..D7 are part of the measurement word

## Python PPK2 adapter

- IRNAS ppk2-api-python: https://github.com/IRNAS/ppk2-api-python
- PyPI package: https://pypi.org/project/ppk2-api/

The dependency is isolated behind `app/ppk/nordic.py`; it is not treated as a permanent architectural dependency.

## Plotly

- Plotly.js: https://plotly.com/javascript/
- Performance guidance: https://plotly.com/python/performance/
