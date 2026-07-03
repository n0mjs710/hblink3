# HBlink3 Access Control Lists (ACLs)

Access Control Lists are one of the most powerful — and most under-documented —
features in HBlink3. They let you decide, packet by packet, **who** may use your
system, **which subscribers** may talk through it, and **which talkgroups** are
allowed on each timeslot. This document explains the ACL grammar, the exact
matching rules (verified against the code in `config.py` and `hblink.py`), a set
of worked examples from simple to advanced, and honest guidance on performance.

ACLs are cheap and safe to use. Read the [Performance](#performance) section and
you will see there is almost no reason *not* to use them.

---

## 1. The two-part grammar

Every ACL is a single string of the form:

```
ACTION:entry,entry,entry,...
```

* **ACTION** is either `PERMIT` or `DENY`.
* **entries** are comma-separated, and each entry is one of:
  * a **single ID** — e.g. `3120101`
  * a **range** — two IDs joined by a hyphen, low first — e.g. `1000-2000`
  * the literal keyword **`ALL`** — matches every possible ID.

Examples of valid ACLs:

```
PERMIT:ALL
DENY:1
DENY:1,1000-2000,4500-60000,17
PERMIT:3120000-3120999,3123456
```

An **empty** value is treated as `PERMIT:ALL` (permit everything).

> There is exactly **one** ACTION per ACL. You cannot mix `PERMIT` and `DENY`
> entries in the same list. If you need "allow these but block those," you layer
> two ACLs — see [Section 6](#6-layering-global-and-system-acls).

---

## 2. The single most important rule: what happens to IDs you *didn't* list

This is the part that trips everyone up, so read it twice.

An ACL does two things at once. It defines an ACTION for the IDs you **list**,
and it *implies the opposite ACTION* for every ID you **don't** list.

| You write | A **listed** ID is… | An **unlisted** ID is… | Mental model |
|-----------|--------------------|------------------------|--------------|
| `PERMIT:…` | permitted | **denied** | **whitelist** — only these get in |
| `DENY:…`   | denied    | **permitted** | **blacklist** — everyone except these |

So:

* `PERMIT:3120100-3120199` means *"allow only IDs 3120100–3120199, block the
  rest of the world."*
* `DENY:3120100-3120199` means *"block IDs 3120100–3120199, allow everyone
  else."*

A listed ID gets the ACTION; an unlisted ID gets the opposite.

**Rule of thumb:** use `DENY:` when you want to block a small, known set and let
everyone else through. Use `PERMIT:` when you want a closed system where only an
explicit list is allowed.

`PERMIT:ALL` and `DENY:1` (deny the reserved ID 1, permit all real IDs) are the
two most common "effectively open" settings.

---

## 3. The four ACL types

HBlink3 applies four kinds of ACL. Each is a separate config key.

| Key | Gates | Value domain | Notes |
|-----|-------|--------------|-------|
| `REG_ACL` | **Repeater/peer registration** (login) | peer IDs, `1 … 4294967295` | Only meaningful on **server (master)** systems. Peers/OpenBridge don't register. **Always enforced** — see §5. |
| `SUB_ACL` | **Subscriber** (the source radio ID of a call) | subscriber IDs, `1 … 16776415` | Applied to every voice/data stream. |
| `TGID_TS1_ACL` | **Destination talkgroup on Timeslot 1** | talkgroup IDs, `1 … 16776415` | |
| `TGID_TS2_ACL` | **Destination talkgroup on Timeslot 2** | talkgroup IDs, `1 … 16776415` | |

The value domains matter: subscriber and talkgroup IDs are checked against a
maximum of **16776415** (`const.ID_MAX`), while registration (peer) IDs go up to
**4294967295** (`const.PEER_MAX`). An entry outside the valid range causes
HBlink3 to **exit at startup** with an `ACL CREATION ERROR` — this is a
guardrail, not a bug (see [Warnings](#warnings)).

---

## 4. Where each ACL is enforced

For an inbound **DMRD voice/data** frame, HBlink3 checks (in order):

1. GLOBAL `SUB_ACL` — source subscriber
2. GLOBAL `TGID_TS1_ACL` or `TGID_TS2_ACL` — destination TG, by slot
3. SYSTEM `SUB_ACL` — source subscriber
4. SYSTEM `TGID_TS1_ACL` or `TGID_TS2_ACL` — destination TG, by slot

The call is dropped at the **first** ACL that denies it. It must pass **all
four** to be forwarded.

For **registration** (a repeater logging into a master), both the GLOBAL and the
SYSTEM `REG_ACL` must permit the peer — if either one denies it, the login is
refused.

### OpenBridge special case

OpenBridge systems do not register (no `REG_ACL`), and **all OpenBridge traffic
is carried on Timeslot 1**. That means the **global `TGID_TS1_ACL`** is your
talkgroup filter for OpenBridge peers. TS2 is forced to `PERMIT:ALL` internally
for OpenBridge systems.

---

## 5. The `USE_ACL` switch — and the one ACL it can't turn off

Each stanza has a `USE_ACL` boolean.

* `USE_ACL: True` — enforce this stanza's `SUB_ACL` and TGID ACLs.
* `USE_ACL: False` — skip the subscriber and talkgroup checks for this stanza
  (traffic passes freely).

**Registration ACLs are always enforced.** `REG_ACL` is *not* governed by
`USE_ACL`. Even with `USE_ACL: False`, a master will still refuse a repeater that
its `REG_ACL` denies. This is by design: registration is your front door.

> Note: the stanzas still require the ACL keys to be present even when
> `USE_ACL: False`. Leave them at `PERMIT:ALL` if unused.

---

## 6. Layering GLOBAL and SYSTEM ACLs

Because GLOBAL is checked first and SYSTEM second, and **both must pass**, you
can compose behaviors that a single `PERMIT`/`DENY` line cannot express. Think of
GLOBAL as your site-wide policy and SYSTEM as per-connection refinement.

**Example — a global blacklist plus a per-system whitelist.**

```ini
[GLOBAL]
USE_ACL: True
SUB_ACL: DENY:1,2,3               # site-wide: block a few abusive IDs, allow the rest

[LOCAL-CLUB-REPEATER]
USE_ACL: True
SUB_ACL: PERMIT:3120000-3120999   # this repeater: ONLY our club's ID block
```

A subscriber must (a) not be one of the globally blocked IDs **and** (b) fall
inside the club's block. The intersection is exactly what you want, and neither
ACL alone could express it.

---

## 7. Worked examples

All examples assume `USE_ACL: True` in the relevant stanza.

**Open system, tidy.** Allow every real subscriber, block only the reserved
ID 1:

```ini
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
```

**Closed talkgroup policy on TS1.** Permit only a curated set of statewide and
local talkgroups on Timeslot 1; leave TS2 open:

```ini
TGID_TS1_ACL: PERMIT:8,9,3100-3199,31201,31203
TGID_TS2_ACL: DENY:1
```

`3100-3199` covers the whole regional block in one range; `8`, `9`, `31201`,
`31203` are individual talkgroups. Everything else on TS1 is dropped.

**Ban a handful of subscribers network-wide** while permitting everyone else:

```ini
[GLOBAL]
SUB_ACL: DENY:1,2606234,3141592,3120555
```

**Members-only repeater.** Only your membership ID range may key up, and only on
approved talkgroups:

```ini
[MEMBERS-ONLY]
USE_ACL: True
SUB_ACL: PERMIT:3120000-3120499
TGID_TS1_ACL: PERMIT:2,9,3120
TGID_TS2_ACL: PERMIT:3120,31205
```

**Registration control on a master.** Accept only two specific repeater IDs onto
this master; reject all other login attempts:

```ini
[MASTER-1]
MODE: SERVER
REG_ACL: PERMIT:311111,311222
```

**Large but efficient range list.** Contiguous and adjacent ranges are merged at
load time, so this…

```ini
SUB_ACL: PERMIT:1000-1999,2000-2999,3000-3999,3120101
```

…collapses internally to a single span `1000–3999` plus the single ID `3120101`.
Write ranges however is clearest to *you*; the engine tidies them up.

---

## Performance

**Short version: turn ACLs on. They are fast.**

* All of the work of reading and organizing your ACLs happens **once, when
  HBlink3 starts** — not while traffic is flowing.
* Checking a packet is near-instant, and it stays fast no matter how long your
  lists get. A list with a thousand entries is checked essentially as quickly as
  a list with ten. You will not see the difference on real traffic.
* Runs of consecutive IDs are **combined** internally, so a config that looks
  huge often takes very little memory.

Where cost actually comes from — and it is small:

* **Every enabled ACL type adds one check per packet.** With GLOBAL + SYSTEM
  layering that's up to four checks per frame. Constant, tiny, predictable.
* **Very large lists of thousands of individual (non-consecutive) IDs** use a
  little more memory and startup time, but checking them stays fast.

Practical guidance:

* Use ranges (`1000-2000`) instead of listing many consecutive IDs — it's less to
  type and smaller in memory.
* If a stanza genuinely uses no ACLs, `USE_ACL: False` skips the subscriber/TGID
  checks entirely (registration is still enforced).
* Don't hesitate to write expressive ACLs. The design specifically optimizes for
  exactly this. The "consumes packet processing time" caution in the sample
  config predates the current implementation; in practice the cost is negligible.

---

## Warnings

* **`PERMIT` is a whitelist — it denies everyone you didn't list.** The most
  common mistake is writing `PERMIT:3120101` intending "also allow 3120101" and
  accidentally locking out the entire rest of the world. Re-read [Section 2](#2-the-single-most-important-rule-what-happens-to-ids-you-didnt-list).
* **Out-of-range entries stop HBlink3 at startup.** A subscriber/TGID entry above
  16776415, or a peer entry above 4294967295, triggers
  `ACL CREATION ERROR, VALUE OUT OF RANGE` and the program exits. Check your
  numbers if HBlink3 won't start after an ACL edit.
* **Write ranges low-to-high** (`1000-2000`, not `2000-1000`). A reversed range
  will silently never match anything.
* **`REG_ACL` ignores `USE_ACL`.** You cannot "disable" registration filtering by
  setting `USE_ACL: False`; set `REG_ACL: PERMIT:ALL` if you want it open.
* **GLOBAL and SYSTEM ACLs are ANDed.** If a call is mysteriously dropped, check
  the GLOBAL stanza too — a global ACL can veto traffic a permissive system ACL
  would otherwise allow.
* **The keys must exist even when unused.** Leave unused ACLs at `PERMIT:ALL`
  rather than deleting them.
