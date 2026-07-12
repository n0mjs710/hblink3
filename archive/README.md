# archive/

Legacy, unmaintained code kept for reference. **None of it runs against the
current asyncio core**, and it is excluded from packaging (`pyproject.toml`).
Nothing in the live code path imports anything here.

| File | What it was | Why it's here |
|---|---|---|
| `bridge_all.py` | A "forward-everything" proxy (every system bridged to every other). | Never ported to the asyncio core — still imports the Twisted-era `reportFactory`, so it fails at import. |
| `playback.py` | A talkback/parrot app: connected like a repeater and played a call stream back so users could hear themselves. | Twisted-based. **Earmarked to return as an OmniLink adapter** — running it as a separate process that connects like a repeater (tied to a system or bridge) keeps the repeat-back off the routing hot path, which is why it was standalone here too. |
| `play_ambe.py` | Played canned AMBE/voice sequences into a system for testing. | Twisted-based; depends on the voice utilities below. |
| `mk_voice.py` | Built synthetic voice/AMBE packet sequences. | Only used by `play_ambe.py`. |
| `voice_lib.py` | Word/AMBE sample library for the voice utilities. | Only used by `mk_voice.py` / `play_ambe.py`. |
| `reporting_const.py` | `REPORT_OPCODES` for the old pickle/opcode reporting protocol. | Superseded by the newline-delimited-JSON reporting feed; no longer referenced. |

If any of these is revived, port it to the asyncio core and the NDJSON reporting
feed first.
