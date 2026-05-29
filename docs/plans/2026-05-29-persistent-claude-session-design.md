# web_cli_bridge — Persistent Interactive `claude` Session (DRAFT design)

> Status: **DRAFT, not yet user-validated.** Produced 2026-05-29 by a 12-agent design
> workflow (4 investigation + 1 synthesis + 6 red-team + 1 corrected synthesis; 47
> findings). Decisions locked with the user: full visible TUI; automated caller (the
> rewritable rwa); tmux invisible/background; rwa dependency-free; transport HTTP
> SSE-down + POST-up (no websockets); **file rendezvous** for structured output (no
> second LLM in the envelope path). Open questions at the bottom still need the user.

## (0) Architecture overview

A long-lived `claude` TUI runs inside a **detached tmux session, one tmux server per
session** (`-L wcb_<id>`), so it survives bridge restarts and request-thread lifetimes —
tmux, not the bridge, owns the process. The bridge shells out to the `tmux` binary via
argv lists (`shell=False`, **zero new Python deps**); the existing stateless `/run` and
`/stream` paths are untouched. A new `/session/*` route family adds lifecycle + per-turn
SSE. The in-memory registry is a **reconstructable cache over `tmux list-sessions`**, never
the source of truth for liveness.

Structured output uses **file rendezvous gated on a `.part`→rename completion edge**
(never screen quiescence): claude *reads* a per-turn `doc.<turn_uuid>.html` and *writes*
`env.<turn_uuid>.json`, which the bridge reads back **byte-exact** with `O_NOFOLLOW` +
owner/mode/nlink checks. The screen is read **only** for state (thinking / awaiting-input /
idle / dead) and deltas — never for the payload. Security baseline: every `/session/*`
endpoint is **token-mandatory, origin-allowlisted (not `*`), and per-session
capability-bound** (`cap`), with all caller-supplied ids strictly whitelisted before they
touch a path or argv.

```
  ┌────────────────┐   POST /session/* (control + send-key)     ┌──────────────────────────────┐
  │  rwa (browser) │ ─────────────────────────────────────────▶ │  web_cli_bridge (server.py)   │
  │ modifyViaBridge│                                             │  ThreadingHTTPServer @127:8765 │
  │ dependency-free│ ◀── SSE: thinking|streaming|awaiting_input| │  ┌─────────────────────────┐   │
  └───────┬────────┘        idle|done events ─────────────────── │  │ _Registry (dict+Locks)  │   │
          │                                                      │  └───────────┬─────────────┘   │
          │ put-doc / get-envelope                               │  subprocess │ tmux binary      │
          ▼                                                      └─────────────┼──────────────────┘
   ┌────────────────────┐  capture-pane / send-keys / pipe-pane                ▼
   │ rendezvous dir 0700│ ◀──────────────────────────────────────────  ┌───────────────────────┐
   │ doc.<uuid>.html  → │                                               │ detached tmux session  │
   │ env.<uuid>.json  ← │ ◀── claude Read / Write tool ───────────────  │  pane: `claude` TUI    │
   └────────────────────┘                                               │  (Ink, alternate_on=0) │
                                                                        └───────────────────────┘
```

---

## (1) Session lifecycle & registry

**Identity.** The bridge mints `session_id = uuid4().hex` and never trusts a caller-supplied
id for anything path-bound; any presented id is validated with `re.fullmatch(r'[0-9a-f]{32}')`
→ else 400. All durable names derive from it: tmux session `t` on socket `wcb_<id>`;
rendezvous dir `<base>/wcb_<id>_<created_nonce>/` (per-incarnation nonce so a restarted
session never reuses a stale dir). Each session also gets an unguessable `cap`
(`secrets.token_hex`) stored in the 0700 dir; **every** `/session/*` call must present a
matching `session_id` + `cap` (closes drive-by hijack and cross-tab eavesdropping).

**Create** — `POST /session/create {cwd?, cols?, rows?}` → `{session_id, cap, rendezvous_dir, created_at}`:
- `os.path.isdir(cwd)` + realpath-confine (tmux silently falls back to `$HOME` otherwise).
- Defensively clear the socket path: `lstat`; if it exists and is **not** a socket → unlink;
  if it is a socket → `has-session`, unlink on failure (a leftover non-socket file otherwise
  permanently bricks re-create).
- `tmux -L wcb_<id> -f /dev/null new-session -d -s t -c <cwd> -x <cols> -y <rows>`; resolve `%N`.
- Persist `@wcb_created` / `@wcb_nonce` in tmux options (survive restart, no Python state).
- `pipe-pane` retention to a private log created `O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW, 0o600`
  then a `shlex.quote`d `cat >>` (`%`→`pct` in the filename).
- Verify `#{alternate_on} == 0` once (the whole inline-capture model depends on it).
- Launch claude by typing it, then a **positive readiness probe** (poll ≤30 s for the composer
  footer present AND no `❯ 1.` menu; detect + handle the workspace-trust prompt explicitly) —
  **never** declare ready by quiescence.

**List** — `GET /session/list`: gather tmux facts first, then under the structural lock apply
only additive `setdefault`; mark suspected-dead for a *separate confirmed* reaper pass; never
evict an id whose turn `lock.locked()`. `state` reads `busy` whenever the turn lock is held.

**Reconstruct after restart** — `get_or_reconstruct(id)` uses a **placeholder pattern**: under
the structural lock, `setdefault` a `RECONSTRUCTING` `_Session` whose turn lock is created now;
the winning thread hydrates via tmux I/O *outside* the lock and sets a per-session `Event`;
losers block on the Event and share the one object/lock (kills the dual-lock race). Liveness is
`has-session` **only**; missing `@wcb_*` options are recoverable defaults, never "dead".

**Delete** — `POST /session/delete`: realpath-confirm the dir is under base, then `kill-server`,
unlink the socket, and `rm -rf` the dir + log **only if confined**.

**Registry & locking.** Module-level `_Registry` = `dict` + a structural `threading.Lock`
(guards dict mutations only, **never** wraps tmux I/O or a streaming loop) + a per-`_Session`
turn `Lock` that serializes turns. Turn lock uses a bounded `acquire(timeout≈2.0)` *before*
`send_response(200)` → 409 "session busy" if held; released in `finally` and **never** kills
the session (a turn ending ≠ the session dying).

---

## (2) SSE turn protocol

**Endpoints:** `POST /session/stream` (SSE), `POST /session/send-key`, `POST /session/capture`,
`POST /session/interrupt`, `POST /session/replay`, `GET /session/list`. SSE headers + auth
(`hmac.compare_digest` + non-ASCII guard) reuse the existing machinery. **CORS is not `*`** —
reflect `Access-Control-Allow-Origin` only for the configured rwa origin and reject other
`Origin`/`Sec-Fetch-Site` with 403 *before* any tmux work (a `*`-CORS + token-optional default
would be a drive-by RCE).

**`/session/stream`** `{session_id, cap, doc?, input?, enter?, timeout?, from_offset?}` runs
**one turn as a single critical section** holding the turn lock from doc-stage → send-keys →
completion → envelope-read, with no release in between (this kills the pipelining/ordering
race). On turn start it sets tmux `@wcb_turn=<turn_uuid>` so "busy" survives a restart;
on reconstruct, busy = `lock.locked()` OR `@wcb_turn` set OR `FSM != idle`.

Prompt is typed line-split (`send-keys -l ... --`), in-composer newlines via `M-Enter`, one
final bare `Enter` to submit:

```
for i,line in enumerate(text.split("\n")):
    if line: tmux -L <sock> send-keys -l -t %N -- "<line>"
    if i<last: tmux -L <sock> send-keys    -t %N M-Enter   # newline, NOT submit
tmux -L <sock> send-keys -t %N Enter                       # single submit
```

**The completion edge is the FILE, not the screen.** The prompt's last instruction: write
`env.<uuid>.json.part`, then rename to `env.<uuid>.json`. The bridge polls for the **final
name** (`O_NOFOLLOW` fstat, size+mtime stable ≥300 ms, mtime > prompt-send time). Screen is
read only for `awaiting_input`/`dead` + deltas, and classification runs **exclusively on
`capture-pane -p` output** (full CSI/OSC/cursor strip), never the raw log. WORKING signals are
OR'd and positive: env file absent (strongest) OR spinner elapsed-timer **increasing** across
captures OR an interrupt-hint present. Idle is declared only when all WORKING signals are false
**and** the env file is present + stable. Brittle "input-box present" / badge / verb regexes are
explicitly **dropped as gates** (always-on or randomized → false idle).

Events: `state{thinking|streaming|awaiting_input(kind,screen)|idle}`, `delta{chunk}`,
`keepalive{t}` (>10 s silence), `done{reason, state, log_offset, alive}`. `reason` ∈ `idle`
(env present), `idle_no_envelope` (settled but file absent/stale — do **not** read),
`awaiting_input`, `timeout`, `client_gone`, `dead`. The turn ends **without killing the
session**; the lock releases in `finally`.

**`/session/send-key`** answers menus (`["1","Enter"]`, `["Down","Enter"]`, `["Escape"]`,
`["C-c"]`); named keys via a fixed `NAMED_KEYS` allowlist, everything else `-l ... --`. Prefer
folding a permission/plan answer into the *same held-lock* `/session/stream` loop so there is no
release gap between answering and resuming. **`/session/replay`** serves a **closed**
`[from_offset, snapshot_end)` byte slice taken under the turn lock (returns the end offset) —
never read-to-live-EOF.

---

## (3) File-rendezvous envelope + doc-sync

**Base dir** under `$HOME` (e.g. `~/Library/Application Support/WebCLIBridge/rendezvous`),
**not `/tmp`**; on startup `lstat`-verify `S_ISDIR && !S_ISLNK && st_uid==euid && mode==0700`,
refuse otherwise; explicit `chmod` after `makedirs` to defeat umask. Per-session dir via a
`mkdtemp`-style nonce.

**Doc-sync = Strategy C (file rendezvous), rwa is the single source of truth.** The rwa applies
each envelope and mutates the document out-of-band (`data-rwa-id` backfill, `canonLF`,
shape-preserving rewrites), so claude's in-session memory of "the document" goes stale after
every commit. Strategy C makes staleness *structurally impossible*: claude only ever **reads**
`doc.<turn_uuid>.html` (fresh each turn — the prompt says "Read it now, do not rely on memory")
and **writes** `env.<turn_uuid>.json`; the rwa stays the sole writer of the document. Rejected
alternatives: B (unified diff) defeats byte-exact anchors; D (claude owns the file) creates a
two-writer race; A (full doc inline) negates the persistence/caching win — kept only as the
first-turn / Read-unavailable fallback.

**persistDoc is the staged bytes** (critical fix). The rwa must stage exactly what `applyEdits`
returns (post-`injectMissingBlockIds`, post-`canonLF`), **not** the agent's raw `newDoc` — concretely
fix `modifyViaBridge` so it does `const persisted = await applyEdits(...); renderDoc(persisted);`
so DOM, store, and `doc.<uuid>.html` agree. Write the file binary, `\n`-only, asserting no `\r`.
Staging happens **inside the held lock before send-keys** (rename happens-before the prompt),
and any pre-existing `env.<turn_uuid>.json` is unlinked at turn start.

**Generation/sentinel guard.** Embed `<!-- rwa:gen <uuid> -->` outside all frozen/anchorable
zones; the prompt requires copying it (and `turn_uuid`) verbatim into the envelope. The
bridge/rwa **rejects** any envelope whose sentinel/`turn_uuid` ≠ the staged turn's → re-stream,
do not apply (turns a silent mis-target into a loud retry; defeats stale-Read and stale-DSL
drift). Compile + apply against the **same staged bytes** held for the turn, not a re-fetched
`getDoc()`.

**Read-back (byte-exact).**

```python
fd = os.open(env_path, os.O_RDONLY | os.O_NOFOLLOW)
st = os.fstat(fd)
if not (stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
        and st.st_nlink == 1 and not (st.st_mode & 0o077)):
    raise EnvelopeRejected                 # → 422, distinct from envelope_not_written
raw = os.read(fd, ...)                     # exact bytes, returned verbatim to the rwa
obj = json.loads(raw)
assert isinstance(obj.get("tool"), str) and obj.get("envelope")
assert obj.get("turn_uuid") == turn_uuid   # freshness
```

Realpath-assert every constructed path under base before any open/rename/rm.
`POST /session/get-envelope {session_id, cap, turn_uuid}` returns the **original bytes** (no
re-serialization) → `parseBridgeEnvelope` runs on claude's exact bytes. Errors: 404
`envelope_not_written`, 422 `envelope_rejected`, brief retry on `JSONDecodeError`
(`envelope_incomplete`). **No second LLM anywhere** in this path — the bridge is a byte pipe.

---

## (4) Error handling & cleanup

**Transient vs gone.** Only explicit `no server running` / `session not found` / nonzero
`has-session` means gone; `TimeoutExpired`, `EAGAIN`/`ENOMEM` fork failures, and `EINTR` are
**retryable** (1–2× small backoff). Require **two** confirming `has-session` failures spaced
apart before any reaper verdict, plus a ≥30 s grace on `@wcb_created`. **Never** `kill-server` /
`rm -rf` / unlink for a session whose turn `lock.locked()` or whose id is mid-reconstruct — gate
all reaping on (lock free) AND (confirmed gone) AND (grace elapsed).

**DEAD discriminator.** A shell name in the pane is DEAD only if it persists across several
polls > `QUIESCENT_MS`, is corroborated by `has-session`/`#{pane_dead}`/`TmuxError`, AND claude
was seen running at least once this session; before the first confirmed composer, a shell name
means STARTING, not dead.

**Stale files.** Unlink the prior `env.<uuid>.json` at turn start (not only after a successful
get); on reconstruct, sweep ALL leftover `env.*.json` / old `doc.*.html` immediately
(verified-regular `O_NOFOLLOW` unlink), never select by recency, always read the exact
`turn_uuid`. After reconstruct, refuse to stream until the rwa re-stages the current-turn doc
(409 `doc_not_staged`).

**Pipe-pane hygiene.** Before re-arming, query `#{pane_pipe}` and disable with an
argument-less `pipe-pane` first (the `cat >>` form does **not** toggle off) so no second
`cat`/`sh` leaks. The reaper caps each log (e.g. 64 MiB) by rotating to a fresh file while
tracking a cumulative offset base so `from_offset` stays globally monotonic; `/list` exposes
`log_bytes`.

**Lifecycle leaks.** An idle-TTL reaper on `@wcb_last_turn` kills sessions idle > T regardless
of liveness; enforce a max concurrent-session cap at create (429); cache `/list` briefly to avoid
O(N) subprocess fan-out; on tray quit, either `kill-server` all `wcb_*` or rely on the TTL reaper
(documented).

**Timeouts / disconnect.** A per-turn `timeout` releases the lock without killing the session;
SSE `client_gone` (from `_write_sse`→`False`) releases the lock in `finally` and persists the
session for `from_offset` resume. Error ladder: `_BodyTooLarge`→413; value/type/JSON→400;
`_TmuxError`→502; busy→409; tmux-not-installed→503; `EnvelopeRejected`→422; traversal/cap-mismatch→403;
else 500. `/session/interrupt` uses a guarded `killpg(tpgid, SIGINT)` (only if `tpgid>0` and
`!= shell_pid`).

---

## Key risks & mitigations

| # | Finding (sev) | Mitigation |
|---|---|---|
| 1 | `*` CORS + token-optional = drive-by RCE (crit) | Mandatory token + origin allowlist + Origin/Sec-Fetch check on every `/session/*` |
| 2 | Byte-quiescence fires mid-turn; interrupt-hint/input-box unreliable (crit) | `.part`→rename file edge = completion; screen only for await/dead; WORKING = env-absent OR rising spinner timer |
| 3 | persistDoc vs agent `newDoc` divergence; backfilled `data-rwa-id` (crit) | Stage exactly `applyEdits` return; `renderDoc(persisted)`; anchor on `data-rwa-id` |
| 4 | Atomic rename ≠ write-before-Read ordering / pipelining (crit) | One held-lock critical section stage→send→read; per-turn `doc.<uuid>.html`; gen/sentinel echo rejects stale reads |
| 5 | Reconstruct race → dual locks; busy not durable (crit/high) | Placeholder + Event `setdefault`; persist `@wcb_turn`; busy = lock OR option OR FSM |
| 6 | Symlink read / path traversal / cap-less adoption (high) | `O_NOFOLLOW` + fstat owner/mode/nlink; strict id whitelist + realpath-confine; per-session `cap` |
| 7 | Stale env read on finish-without-write / reconstruct (high) | Bridge-minted uuid, exact-name read, mtime>prompt, sweep leftovers, uuid-in-envelope |
| 8 | `/tmp` base hijack; pipe-pane shell sink (high) | `$HOME` base, lstat owner/mode verify; full-path `shlex.quote`; argv `shell=False` |
| 9 | CRLF/canonLF anchor mismatch; DSL compile vs live store (high) | Stage `canonLF(persistDoc)` `\n`-only; compile + apply against the same held bytes |
| 10 | send-key answer races resume (high) | Fold answer into held-lock stream loop; standalone send-key polls effect before release |
| 11 | Non-socket file bricks re-create; meta-missing→false death (high) | Defensive socket-path clear; liveness via `has-session` only; options are defaults |
| 12 | Unbounded/leaked logs, no TTL/cap (high) | Log rotation w/ offset base; idle-TTL reaper; max-session cap; re-pipe idempotency |
| 13 | Transient `TmuxError` → live session reaped (med) | Retryable vs authoritative classes; 2× confirm + grace; never reap busy/reconstructing |
| 14 | `@wcb_rendezvous` redirect; bad `cwd` silent (med) | Re-derive dir from id (option = cross-check); validate `cwd` isdir + confined |

---

## Decisions (validated 2026-05-29)

1. **Doc-sync (Strategy C):** approved — change `modifyViaBridge` (~line 3502) to `renderDoc(persisted)`; the rwa stages the post-backfill `applyEdits` bytes.
2. **Permissions:** keep `--permission-mode bypassPermissions` (matches today's proven backend); the design retains the ability to surface `awaiting_input` prompts later without rework.
3. **Session granularity:** per-document — the rwa mints one `session_id` per document UUID.
4. **Idle policy:** idle-TTL = **2 h**; on tray quit `kill-server` all `wcb_*` (assumed default); max concurrent sessions = **8** (assumed default). Both numbers overridable.
5. **Live calibration:** approved — run one real `claude` session to pin `alternate_on`, spinner/menu/trust strings, and the `M-Enter` newline before writing the implementation plan.
6. **Low-sev deferrals:** confirmed deferred.

## Open questions — RESOLVED (see Decisions above)

1. **Doc-sync confirmation (Strategy C).** Committing to: stage exactly `applyEdits`'
   `persistDoc` return, `canonLF`'d, per-turn `doc.<uuid>.html`, with a gen/sentinel echo. This
   requires editing `modifyViaBridge` (~line 3502) to `renderDoc(persisted)`. **May I change the
   rwa's render path**, or must the bridge adapt to the current pre-backfill render?
2. **Permissions: keep `bypassPermissions` or surface real prompts?** Red-team shows
   `bypassPermissions` does NOT suppress the trust prompt and turns a CORS slip into a durable RCE.
   (a) `bypassPermissions` (fewest round-trips), (b) real prompts as `awaiting_input` for the rwa
   to answer, or (c) a fixed allowlist mode? Also decides whether Write/Read need per-turn approval.
3. **Session granularity: per-doc vs per-cwd vs per-tab.** `cap` makes hijack moot, but identity
   still drives reuse. Which does the rwa mint UUIDs against?
4. **Idle-timeout policy.** TTL `T` for the idle reaper (30 min? 4 h? never?); on tray quit,
   `kill-server` all `wcb_*` or leave for the TTL reaper? Max concurrent-session cap value?
5. **Live verification still needed (deferred until you approve a real session):**
   `alternate_on==0`; `.part`→rename Write reliability + absolute-path targeting under the chosen
   permission mode; `M-Enter` as the in-composer newline (or restrict to single-line prompts);
   pin the CALIBRATE strings (spinner timer format, trust wording, menu wording) for v2.1.156 with
   a regression test.
6. **Deliberately deferred (low sev):** cross-session reuse (covered by per-incarnation nonce +
   `cap`); replay-eavesdrop (covered by `cap`); bad-cwd leak (surfaced as 400 at create).

---

## Calibration results — verified live 2026-05-29 (claude 2.1.156, tmux 3.6a, macOS)

A real `claude --permission-mode bypassPermissions --model haiku` session was driven in a
dedicated tmux server, using the exact `tmux -L … capture-pane / send-keys` calls the bridge
will use. **All three load-bearing assumptions hold:**

- **`alternate_on == 0` at every stage** (pre-launch shell, launched, composer-ready, mid-turn,
  post-exit). claude renders **inline**, not on the alternate screen — so `capture-pane -p` is a
  valid view and the `pipe-pane` log is the full transcript.
- **`bypassPermissions` does NOT suppress the workspace-trust prompt** (red-team was right). First
  launch in a new cwd shows `Quick safety check: Is this a project you created or one you trust?`
  with `❯ 1. Yes, I trust this folder` / `2. No, exit` and `Enter to confirm · Esc to cancel`.
  **Create must detect and answer it** — a single `Enter` accepts the default (`["1","Enter"]` is
  the explicit form). No composer appears until it's answered.
- **Write tool → exact absolute path, byte-exact, no permission prompt.** Asked to write
  `{"ok":true,"probe":"wcbcal"}` to an absolute path, claude produced that file with **exactly 28
  bytes, no trailing newline** (`…wcbcal"}`). File rendezvous works; the bridge reads the bytes verbatim.

**Completion edge confirmed file-not-screen.** The rendezvous file appeared at 12:01:38; at that
*same second* the screen still showed `✽ Smooshing… (3s …)` / `✻ Baked for 5s` with `esc to
interrupt` in the footer — i.e. it still looked "working" after the payload was already complete.
Screen quiescence would have mis-timed the turn; the `.part`→rename file edge is correct.

**Pinned CALIBRATE constants (v2.1.156):**
- WORKING ⇔ footer contains **`esc to interrupt`** (cleanest signal) and/or a spinner line with an
  incrementing elapsed timer `…(<N>s · …)`. The spinner **verb is randomized** (`Smooshing…`,
  `Baked for 5s`, `thinking`) — never match on the verb.
- IDLE/ready ⇔ footer shows **`⏵⏵ bypass permissions on (shift+tab to cycle)`** and **no** `esc to
  interrupt`; the composer is a `❯ ` line framed by two horizontal rules.
- Markers: **`⏺`** (assistant line / tool call, e.g. `⏺ Write(<path>)`), **`⎿`** (tool result).
- `pane_current_command` = **`claude.exe`** while running (native build — NOT `node` as §1/§4
  guessed), reverting to the shell name (`zsh`) on exit. The DEAD discriminator is shell-name-
  *relative*, so the literal value doesn't matter, but the doc's "`node`/`claude`" notes → `claude.exe`.
- **`M-Enter` inserts a composer newline without submitting** (verified: `lineONE`⏎`lineTWO` stayed
  unsent on two lines). Multi-line prompts via per-line `send-keys -l` + `M-Enter` + final `Enter` are sound.
- On `/exit` the pane prints `Resume this session with: claude --resume <uuid>` then the shell prompt returns.

Net: **no design changes required** — the file-edge, trust-prompt handling at create, and the
`claude.exe` process name are folded in. Deferred live items (permission-menu wording for non-bypass
mode, plan-mode screen) stay deferred since we ship with `bypassPermissions`.
