# Party Mode — Multi-Player Architecture

> Last updated: 2026-02-17

## Overview

**Party Mode** enables N human players + 1 AI Dungeon Master (Claudmaster) to play D&D together. Each player connects from their own device (smartphone, tablet, or PC) via a web browser — no software installation required.

The host runs dm20-protocol on their machine. A lightweight web server provides each player with a personal interface: narrative feed, character sheet, action input, and private DM messages — all filtered by the existing permission system.

### What's Already Built

The **data model and permission layer** are complete and tested:

| Component | File | Status |
|-----------|------|--------|
| Role system (DM, PLAYER, OBSERVER) | `src/dm20_protocol/permissions.py` | Done |
| Permission matrix (84 MCP tools) | `src/dm20_protocol/permissions.py` | Done |
| Character ownership enforcement | `src/dm20_protocol/permissions.py` | Done |
| PC tracking and session management | `src/dm20_protocol/claudmaster/pc_tracking.py` | Done |
| Info visibility (PUBLIC/PARTY/PRIVATE/DM_ONLY) | `src/dm20_protocol/claudmaster/private_info.py` | Done |
| Private messaging and hidden rolls | `src/dm20_protocol/claudmaster/private_info.py` | Done |
| Split party handling | `src/dm20_protocol/claudmaster/split_party.py` | Done |
| Output filtering by role | `src/dm20_protocol/output_filter.py` | Done |
| Zero overhead in single-player mode | `src/dm20_protocol/permissions.py` | Done |

### Transport and Presentation Layer

The web server that connects players to the existing game engine:

| Component | Description | Status |
|-----------|-------------|--------|
| Web server (Party Mode) | Starlette/Uvicorn HTTP server with WebSocket support | Done |
| Player web UI | Responsive vanilla HTML/CSS/JS interface for browsers | Done |
| QR code generation | Per-player access tokens encoded as QR PNG | Done |
| Action queue | JSONL-backed action queue with crash recovery | Done |
| Real-time push | WebSocket per-player filtered broadcast with reconnection replay | Done |
| Combat coordination | Turn gating, initiative display, simultaneous mode | Done |
| Host slash commands | 7 commands: party-mode, party-stop, party-next, party-auto, party-status, party-kick, party-token | Done |
| E2E integration tests | 154 tests: permission boundaries, concurrency, stability | Done |
| DM dashboard | Host monitoring web UI (optional, Phase 2B) | Not started |

---

## Architecture

### High-Level Diagram

```
  ┌──────────────────────────── Host Machine ─────────────────────────────┐
  │                                                                       │
  │  ┌──────────────┐         ┌──────────────────┐     ┌───────────────┐ │
  │  │ Claude Code   │◄──MCP──►│ dm20-protocol    │◄────►│ Web Server   │ │
  │  │ (DM brain)    │         │ (state & rules)  │ API  │ (Party Mode) │ │
  │  └──────────────┘         └──────────────────┘     └──────┬────────┘ │
  │                                                           │          │
  └───────────────────────────────────────────────────────────┼──────────┘
                                                              │ HTTP / WS
                                            ┌─────────────────┼────────────┐
                                            │     Local Network (WiFi)     │
                                            │                              │
                                   ┌────────┴────────┐                     │
                                   │                  │                     │
                             ┌─────┴─────┐    ┌──────┴────┐    ┌─────────┐│
                             │ Player 1   │    │ Player 2   │    │Player 3 ││
                             │ (browser)  │    │ (browser)  │    │(browser)││
                             │ phone/PC   │    │ phone/PC   │    │phone/PC ││
                             └───────────┘    └───────────┘    └─────────┘│
                             └─────────────────────────────────────────────┘
```

### Key Principle: Single Brain

There is exactly **one** Claude instance (running in the host's Claude Code). The web server does not run its own AI — it relays player actions to the host and distributes filtered responses back. This ensures:

- **Narrative coherence** — one DM voice, consistent story
- **Zero extra cost** — no additional API calls (Phase 2A)
- **Existing permission system** — output filtering already handles per-player visibility

---

## Phase 2A — Host-Driven MVP

The host's Claude Code session processes all player actions. Players submit actions via browser, the host processes them, and responses are pushed back to each player's browser.

### How It Works

#### 1. Host Starts Party Mode

```
> /dm:start                    # load campaign as usual
> /dm:party-mode               # start web server
```

Output:
```
Party Mode active on http://192.168.1.42:8080
Registered players:
  Thorin  (token: aX7kM9) → QR saved
  Elara   (token: bR3nP2) → QR saved
  Vex     (token: cT5wQ8) → QR saved
QR codes: /tmp/dm20-party/qr-codes/
```

The web server reads the campaign's registered PCs from `PCRegistry` and generates one access token per player.

#### 2. Players Connect

Each player scans their QR code (or types the URL). The QR encodes:

```
http://192.168.1.42:8080/play?token=aX7kM9
```

The token maps to a `player_id` in the permission system. No passwords, no accounts, no installation.

#### 3. Player Submits an Action

```
Player (browser) → HTTP POST /action {token, text}
                 → Web server validates token
                 → Writes to action queue (campaign_dir/party/actions.jsonl)
                 → Acknowledges to player: "Action queued"
```

#### 4. Host Processes Actions

The host's Claude Code session detects queued actions (notification or polling):

```
> /dm:party-next               # process next queued action
  → reads action from queue
  → processes via normal game loop (CONTEXT → DECIDE → EXECUTE → PERSIST → NARRATE)
  → writes response to response queue, tagged with visibility
```

Or in auto mode:
```
> /dm:party-auto               # auto-process actions as they arrive
```

#### 5. Players Receive Responses

```
Web server reads response queue
  → filters response through PermissionResolver + OutputFilter
  → pushes to each player's browser via WebSocket:
     - Thorin sees: public narrative + his private info
     - Elara sees: public narrative + her private info
     - Neither sees DM-only notes
```

### Action Queue Format

```jsonl
{"id": "act_001", "player_id": "thorin", "text": "I approach the table with my hand on my axe", "timestamp": "2026-02-17T20:15:00Z", "status": "pending"}
{"id": "act_002", "player_id": "elara", "text": "I cast Detect Magic while Thorin talks", "timestamp": "2026-02-17T20:15:12Z", "status": "pending"}
```

### Response Queue Format

```jsonl
{"id": "res_001", "action_id": "act_001", "narrative": "Thorin approaches the hooded figure...", "private": {"thorin": "You notice a dagger under his cloak"}, "dm_only": "The figure is actually the BBEG in disguise", "timestamp": "2026-02-17T20:15:30Z"}
```

### Player Web UI

Responsive web interface that works on any screen size:

```
┌─────────────────────────────────────┐
│  Party Mode — Thorin Ironforge      │
├─────────────────────────────────────┤
│                                     │
│  NARRATIVE                          │
│  ─────────────────────────────────  │
│  The Drunken Dragon tavern is       │
│  thick with pipe smoke...           │
│  A hooded figure beckons you        │
│  from the corner table.             │
│                                     │
│  PRIVATE (DM whisper)               │
│  ─────────────────────────────────  │
│  "You notice a dagger hidden        │
│  under his cloak"                   │
│                                     │
│  CHARACTER (tap to expand)          │
│  HP: 45/52  AC: 18  Level: 5       │
│                                     │
├─────────────────────────────────────┤
│  > What do you do?                  │
│  ┌─────────────────────────────┐    │
│  │                             │    │
│  └─────────────────────────────┘    │
│                        [Send]       │
└─────────────────────────────────────┘
```

Sections:
- **Narrative feed** — shared story, scrollable, auto-updates via WebSocket
- **Private messages** — DM whispers visible only to this player
- **Character summary** — HP, AC, level, conditions; expandable to full sheet (read from MCP `get_character`)
- **Action input** — text field + send button
- **Action status** — "Queued", "Processing...", "Done" indicator

### Network Requirements

| Scenario | Setup | Details |
|----------|-------|---------|
| **Same room / WiFi** | None | All devices on the same LAN; works out of the box |
| **Remote players** | `ngrok http 8080` or Cloudflare Tunnel | Host runs 1 command to expose the server; players connect via public URL |
| **Mixed (some local, some remote)** | Tunnel + LAN | Both access methods work simultaneously |

LAN discovery: the web server could optionally broadcast via mDNS (`dm20-party.local`) for zero-config access on the local network.

### Authentication Model

- **Session tokens** — generated at Party Mode startup, one per registered PC
- **Token ↔ player_id mapping** — stored in memory, valid for the session duration
- **QR code** — encodes the full URL including token; scan-and-play
- **No shared credentials** — the host never shares Mac/PC passwords or SSH access
- **Token refresh** — host can regenerate a token if compromised (`/dm:party-token thorin`)
- **Character ID as identity** — the token resolves to a `player_id` which maps to character ownership in `PermissionResolver`

### Supported Devices

| Device | Support | Notes |
|--------|---------|-------|
| Smartphone (iOS/Android) | Full | Responsive UI, QR scan native |
| Tablet | Full | Best experience for character sheets |
| PC/Mac | Full | Keyboard input, multi-window |
| Smart TV | Read-only | Could display shared narrative on a big screen |

### Web Push Notifications

Players can enable "Notify me" to get a notification when the DM replies, even with the tab closed. The server sends these directly to the browser vendor's push service (FCM, Mozilla autopush, Apple `web.push.apple.com`, Windows WNS) using VAPID keys generated once under the campaign's `party/` folder. Subscription endpoints on any other host (or without `https://`) are rejected.

| Setting | Default | Purpose |
|---------|---------|---------|
| `DM20_VAPID_SUBJECT` | `mailto:dm20-party@example.com` | VAPID contact claim (`mailto:` or `https:` URL). Set this to a real address you control: Apple's push service may reject placeholder or non-routable values such as `mailto:dm@localhost`. |

### New Commands (Phase 2A)

| Command | Description |
|---------|-------------|
| `/dm:party-mode` | Start web server, generate tokens and QR codes |
| `/dm:party-stop` | Stop web server, end Party Mode |
| `/dm:party-next` | Process the next queued player action |
| `/dm:party-auto` | Auto-process actions as they arrive |
| `/dm:party-token <player>` | Regenerate access token for a player |
| `/dm:party-status` | Show connected players, pending actions, queue status |
| `/dm:party-kick <player>` | Disconnect a player and invalidate their token |

### Technical Stack (Phase 2A)

| Component | Technology | Reason |
|-----------|------------|--------|
| Web server | Starlette (bundled with FastMCP) | Already a dependency, no new packages |
| WebSocket | Starlette WebSocket support | Built-in, no extra dependency |
| QR generation | `qrcode` Python package | Lightweight, generates PNG/SVG |
| Frontend | Vanilla HTML + CSS + JS | No build step, no Node.js required |
| Action queue | JSONL file in campaign directory | Simple, persistent, human-readable |
| Real-time push | WebSocket per connected player | Low latency, bidirectional |

---

## Phase 2B — Autonomous Claudmaster (Future Vision)

Phase 2A requires the host to be active in Claude Code to process actions. Phase 2B removes this requirement: an autonomous Claudmaster agent processes player actions directly.

### Architecture Change

```
  ┌──────────────────────────── Host Machine ─────────────────────────────┐
  │                                                                       │
  │  ┌──────────────┐         ┌──────────────────┐     ┌───────────────┐ │
  │  │ Claudmaster   │◄──API──►│ dm20-protocol    │◄────►│ Web Server   │ │
  │  │ Agent         │         │ (state & rules)  │ API  │ (Party Mode) │ │
  │  │ (Claude API)  │         └──────────────────┘     └──────┬────────┘ │
  │  └──────┬───────┘                                         │          │
  │         │ Claude API                                      │          │
  │         ▼                                                 │          │
  │  ┌──────────────┐                                         │          │
  │  │ DM Dashboard  │ (optional: host monitors via browser)  │          │
  │  └──────────────┘                                         │          │
  └───────────────────────────────────────────────────────────┼──────────┘
                                                              │ HTTP / WS
                                                         [Players]
```

### Key Differences from Phase 2A

| Aspect | Phase 2A (Host-Driven) | Phase 2B (Autonomous) |
|--------|------------------------|----------------------|
| DM brain | Claude Code on host | Claudmaster agent (Claude API) |
| Host involvement | Must be active | Can walk away |
| Response latency | Depends on host | Near-instant (API call) |
| Cost | Zero extra (Claude Pro/Max) | API usage cost per action |
| Narrative quality | Full Claude Opus context | Depends on agent architecture |
| Setup complexity | Low | Medium (API key required) |

### Requirements for Phase 2B

1. **Claude API key** — the host configures an Anthropic API key; the web server uses it to call Claude
2. **Claudmaster agent maturity** — the existing Narrator/Archivist/Arbiter pipeline must handle autonomous play without supervision
3. **Context management** — the agent must maintain conversation history across player actions without exceeding context limits
4. **Cost controls** — token budget per session, cost estimation before starting, optional confirmation for expensive operations
5. **DM dashboard** — web UI for the host to monitor the game, intervene if needed, and override Claudmaster decisions
6. **Fallback to 2A** — if the API key runs out of credits or Claudmaster gets confused, graceful fallback to host-driven mode

### Claudmaster Agent Flow (Phase 2B)

```
Player action arrives via WebSocket
  → Web server receives action
  → Constructs prompt: game state + recent history + player action
  → Calls Claude API with MCP tool access
  → Claude processes: CONTEXT → DECIDE → EXECUTE → PERSIST → NARRATE
  → Response filtered by PermissionResolver
  → Pushed to player browsers via WebSocket
  → DM dashboard updated
```

### Cost Estimation Model

Rough estimates for a typical session (3-4 hours, 4 players):

| Model | Input tokens/action | Output tokens/action | Actions/session | Est. cost/session |
|-------|--------------------|--------------------|----------------|-------------------|
| Haiku | ~2K | ~500 | ~100 | ~$0.10 |
| Sonnet | ~2K | ~500 | ~100 | ~$1.50 |
| Opus | ~2K | ~500 | ~100 | ~$7.50 |

A tiered approach (Haiku for simple actions, Sonnet for narrative, Opus for complex decisions) could optimize cost.

### DM Dashboard Features (Phase 2B)

- Real-time game log (all actions and responses)
- Player connection status
- Override button (take manual control of next response)
- Pause/resume Claudmaster
- Inject DM narration or private messages
- Token budget and cost tracker
- Session recording and export

---

## Transport Alternatives Considered

### Option 1: Pure MCP Streamable HTTP

Each player connects with their own MCP client (Claude Code, Claude Desktop).

**Rejected because:**
- Each player needs an AI client + Claude subscription
- Multiple independent Claude instances = narrative incoherence
- High cost and complexity for casual play
- Not accessible from a phone browser

### Option 3: Turn-Based Async on Same Connection

Players take turns at the same machine.

**Rejected as primary mode because:**
- Requires physical presence at the same computer
- No privacy (everyone sees the same screen)
- Slow and cumbersome

**Note:** Could be supported as a fallback mode alongside Party Mode.

### Option 2 (Selected): Web Relay

A lightweight web server on the host machine provides browser-based access for all players.

**Selected because:**
- Zero installation for players (just a browser)
- Works on any device (phone, tablet, PC)
- Single DM brain = narrative coherence
- QR code access = frictionless onboarding
- Builds on existing permission and visibility systems
- LAN-first with optional remote access via tunnel
- Progressive: starts as MVP (2A), evolves to autonomous (2B)

---

## Implementation Order

### Phase 2A — MVP (Host-Driven)

```
Step 1: Web server skeleton
  → Starlette app with basic routes
  → /play endpoint serves player UI
  → /action endpoint receives player actions
  → WebSocket endpoint for real-time push

Step 2: Authentication
  → Token generation at party-mode startup
  → Token validation middleware
  → QR code generation (qrcode package)

Step 3: Action queue
  → JSONL-based action queue in campaign directory
  → /dm:party-next command reads and processes queue
  → Response queue for filtered outputs

Step 4: Player web UI
  → Responsive HTML/CSS/JS (no framework, no build step)
  → Narrative feed (auto-scroll, WebSocket updates)
  → Character sheet view (read from get_character)
  → Action input with send button
  → Private message display

Step 5: Integration
  → Wire WebSocket push to response queue
  → Wire action queue to dm:party-next / dm:party-auto
  → Permission filtering on all outgoing data
  → QR code display in terminal

Step 6: Polish
  → mDNS discovery (optional)
  → Token refresh command
  → Party status command
  → Error handling and reconnection
```

### Phase 2B — Autonomous Claudmaster

```
Step 1: API integration
  → Claude API client in web server
  → Prompt construction from game state + history

Step 2: Autonomous game loop
  → Action → Claude API → MCP tools → response → push
  → Context window management across actions

Step 3: DM dashboard
  → Host monitoring web UI
  → Override and intervention controls
  → Cost tracking

Step 4: Cost optimization
  → Model tiering (Haiku/Sonnet/Opus per action type)
  → Token budget and alerts
  → Fallback to Phase 2A when budget exhausted
```

---

## Related Resources

- **Permission system**: `src/dm20_protocol/permissions.py` — `PlayerRole`, `PermissionResolver`, permission matrix
- **PC tracking**: `src/dm20_protocol/claudmaster/pc_tracking.py` — `PCRegistry`, `MultiPlayerConfig`, `PCIdentifier`
- **Private info**: `src/dm20_protocol/claudmaster/private_info.py` — `PrivateInfoManager`, `InfoVisibility`
- **Split party**: `src/dm20_protocol/claudmaster/split_party.py` — `SplitPartyManager`
- **Output filtering**: `src/dm20_protocol/output_filter.py` — role-based response filtering
- **PRD**: `.claude/prds/campaign-experience-enhancement.md` — original multi-user requirements (US-4, US-5, FR-11 to FR-18)
- **Claudmaster PRD**: `.claude/prds/claudmaster-ai-dm.md` — US-6 (Multi-Player Session), FR-8 (Multi-Player Support)
- **FastMCP HTTP transport**: `docs/FastMCP_2.9.0_docs.md` — Streamable HTTP documentation
