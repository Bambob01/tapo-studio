# Third-party notices

Tapo Studio is released under the MIT licence (see `LICENSE`).
It is an independent project and is **not affiliated with, endorsed by, or
sponsored by TP-Link**. "Tapo" and "TP-Link" are trademarks of their owners and
are used here only to say which devices the software works with.

## Included data

**`tapo_studio/data/lighting_presets.json` and `segment_presets.json`** hold the
parameters of the light strip's built-in effects and scenes. They were
transcribed from
[`tapo/src/requests/set_device_info/lighting_effect.rs`](https://github.com/mihai-dinculescu/tapo/blob/main/tapo/src/requests/set_device_info/lighting_effect.rs)
and `segment_effect.rs` in the `tapo` project using `tools/extract_presets.py`.
That source is:

```
MIT License
Copyright (c) 2022-2025 Mihai Dinculescu
```

## Runtime dependencies (installed from PyPI, not bundled)

| Package | Licence | Used for |
|---|---|---|
| [tapo](https://github.com/mihai-dinculescu/tapo) | MIT | Talking to the devices |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | Web server framework |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | Web server |
| [NumPy](https://numpy.org) | BSD-3-Clause | Audio and colour maths |
| [soundcard](https://github.com/bastibe/SoundCard) *(optional)* | BSD-3-Clause | Music mode: capturing PC audio |
| [mss](https://github.com/BoboTiG/python-mss) *(optional)* | MIT | Screen sync: capturing the screen |

## Acknowledgements

- [`tapo`](https://github.com/mihai-dinculescu/tapo) by Mihai Dinculescu, the library that makes all of this possible.
- [`tapoviz`](https://github.com/The-DarkMatter/tapoviz) (MIT) pioneered audio and screen reactive control of these strips. Its write-ups of the device's quirks (the 32-hex-character effect id rule, the ~300 ms write time, one-request-at-a-time streaming) informed our design. No code was copied.
- [`tapo-tpap-client`](https://github.com/raymondlo84/tapo-tpap-client) documented, from live testing on an L930-5 with firmware 1.4.3, how that firmware must be painted: the scene (segment) request with type `none`, a full list of zone indexes, one colour per zone, at most 4 display colours. We follow those rules and verify them with a read-back. No code was copied.
- [`python-kasa`](https://github.com/python-kasa/python-kasa) (GPL-3.0) was consulted as protocol documentation. No code or data from it is included.
