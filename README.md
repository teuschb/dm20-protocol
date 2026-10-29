# DM20 Protocol

![DM20 Protocol](docs/assets/DM20%20Protocol.png)

A comprehensive [Model Context Protocol](https://modelcontextprotocol.io/) server for managing AI-assisted Dungeons & Dragons campaigns, built with **FastMCP 2.9+**.

> **This is a fork.** The original project is [BNDYH/dm20-protocol](https://github.com/BNDYH/dm20-protocol) by Polloinfilzato and the DM20 Protocol contributors; all credit for the server belongs to them. This fork's `main` branch adds a small set of patches (party-mode UI fixes, Web Push notifications, limited-use feature tracking, SRD 2024 fixes) on top of upstream `main`. For the canonical version, issues, and releases, use the upstream repository.

- **For Groups** — A toolkit to help run campaigns more effectively
- **For Solo Players** — A complete virtual D&D experience with AI as the DM
- **For Worldbuilders** — Tools to create rich, interconnected game worlds

> **Status:** Under active development. See [Roadmap](docs/ROADMAP.md) for what's next.

## Features

- **Campaign Management** — Create and switch between multiple campaigns
- **Character Builder** — Auto-populated characters from loaded rulebooks (Standard Array, Point Buy, Roll 4d6, Manual). Starting spells and properly typed equipment auto-populated from rulebook data. Creation dice rolls recorded on the character sheet
- **Character Creation Wizard** — Guided step-by-step or Quick Build modes. Asks player name, character level, race, class, ability scores, skills, equipment, and spells. Completeness validation after creation ensures nothing is missing
- **Level-Up & Progression** — Automatic HP, class features, spell slots, ASI/feats on level-up
- **Character Sheets** — Full D&D 5e stats, inventory, spellcasting, death saves, creation roll history
- **Sheet Sync** — Bidirectional Markdown↔JSON sync: edit YAML frontmatter in Obsidian or any editor, DM approves changes
- **D&D Beyond Import** — Import characters from D&D Beyond (URL or JSON file)
- **Rest & Recovery** — Long rest, short rest with hit dice, spell slot management, death saves
- **NPCs & Locations** — Rich world-building with relationships and connections
- **Quest Tracking** — Objectives, status, rewards, and branching paths
- **Combat System** — Initiative, turns, attack/spell resolution, active effects, concentration tracking, ASCII tactical maps, AoE targeting, encounter builder
- **Compendium Packs** — Export/import campaign content as portable JSON packs with conflict resolution (skip, overwrite, rename), selective filtering, and full backups
- **Narrative Fog of War** — Progressive location discovery (undiscovered → glimpsed → explored → fully mapped), sensory hints for hidden features, narrator-aware descriptions
- **Party Knowledge** — Track what the party knows about the world, with source attribution and bidirectional NPC knowledge sharing
- **Multi-User Permissions** — Role-based access (DM, Player, Observer) with output filtering, session coordination, and private messaging — zero overhead in single-player
- **Party Mode** — Multi-player web relay: players connect from their phones/tablets via QR code, each getting a personal game interface with narrative feed, character sheet, and action input — all filtered by the permission system. WebSocket real-time push, combat turn coordination, reconnection with message replay, and JSONL queue persistence
- **Rules Edition Selection** — Choose between D&D 2024 revised rules and 2014 classic rules when creating a campaign. Both SRD and 5etools data sources loaded automatically
- **Multi-Source Rulebooks** — Load rules from SRD, Open5e, 5etools, or custom JSON
- **PDF Rulebook Library** — Import and query your own PDFs and homebrew content
- **Bilingual Play** — Italian/English D&D terminology resolution (500+ terms)
- **Session Notes** — Per-session summaries, XP, loot, attendance
- **Adventure Log** — Searchable timeline of all campaign events
- **Context Auto-Refresh** — `/dm:refrill` saves the session and provides instructions to clear context. Two-layer protection: proactive DM trigger at ~65% + automatic PreCompact hook at ~83.5%
- **Dice & Utilities** — Rolls, XP calculations, rules lookup
- **86 MCP Tools** — Full list in the [User Guide](docs/GUIDE.md)

## Installation

### 3 Steps to Play D&D

You don't need to install anything beforehand. One command sets up everything:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Polloinfilzato/dm20-protocol/main/install.sh)
```

The installer asks a few questions (which MCP client you use, where to put your data), handles all dependencies automatically, and configures everything. When it's done:

```bash
cd ~/dm20    # go to your play directory
claude       # start Claude Code — the MCP server connects automatically
```

That's it. You're ready to play. Type `/dm:help` to see what you can do, or jump straight in with `/dm:start`.

> **Using Claude Desktop instead?** Just restart it after installation — the MCP server is already configured. Use the MCP tools directly instead of slash commands.

The installer offers two modes. **Most users should pick "User"** — it's the default:

| | User (recommended) | Developer |
|---|---|---|
| **Who it's for** | Players who want to play D&D | Contributors who want to modify the code |
| **What it installs** | A single `dm20-protocol` command | Full source code repository |
| **Disk footprint** | Minimal (~50 MB) | Full dev environment (~200+ MB) |
| **Prerequisites** | None (auto-installed) | None (auto-installed) |
| **How to update** | Re-run the install command with `--upgrade` | `git pull && uv sync` |
| **Add voice narration** | Re-run with `--voice` | `uv sync --extra voice` |

> Running `bash install.sh` from inside an existing clone? The installer auto-detects it and switches to Developer mode.

**Supported platforms:** macOS (Apple Silicon & Intel), Linux (x86_64/arm64), Windows via WSL. See [Installer Details](docs/INSTALLER.md) for the full breakdown.

**Want to know what happens under the hood?** The [Installer Guide](docs/INSTALLER.md) covers the full architecture, every edge case we handle, and why we built it this way.

### Compatibility

This server implements the open [Model Context Protocol](https://modelcontextprotocol.io/) standard. It works with **any MCP-compatible client** — not just Claude.

| Platform / Client | Status |
|---|---|
| macOS + Claude Code | **Tested** (Intel & Apple Silicon) |
| macOS + Claude Desktop | **Tested** (Intel & Apple Silicon) |
| Linux + Claude Code | Supported, community testing welcome |
| Linux + Claude Desktop | Supported, community testing welcome |
| Windows (via WSL) | Supported, community testing welcome |
| Cursor, Windsurf, Cline, VS Code Copilot | Supported, community testing welcome |
| OpenAI Codex, Gemini CLI, Amazon Q | Supported, community testing welcome |

> We're confident in cross-platform support (the installer and server are designed for it), but we can only mark combinations as "Tested" once a contributor confirms them. If you try one of the untested combinations and it works (or doesn't), please [open an issue](https://github.com/Polloinfilzato/dm20-protocol/issues) — it helps everyone.

For detailed per-client setup instructions, config file locations, and platform-specific notes, see the **[MCP Client Setup Guide](docs/MCP_CLIENTS.md)**.

### Manual Install

<details>
<summary><strong>Option A: Install as a tool (simplest — no git clone needed)</strong></summary>

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv tool install "dm20-protocol @ git+https://github.com/Polloinfilzato/dm20-protocol.git"
```

Then add to your MCP client's config file (see [MCP Client Setup Guide](docs/MCP_CLIENTS.md) for your client's config path):

```json
{
  "mcpServers": {
    "dm20-protocol": {
      "command": "dm20-protocol",
      "env": {
        "DM20_STORAGE_DIR": "/path/to/your/data"
      }
    }
  }
}
```

> **Note:** Some clients (like Claude Desktop) don't inherit your shell PATH. Use the absolute path instead: `"command": "/Users/you/.local/bin/dm20-protocol"` (find it with `which dm20-protocol`).

On Linux, if `dm20-protocol` is not found after install, run `uv tool update-shell` or add `~/.local/bin` to your PATH manually.

</details>

<details>
<summary><strong>Option B: Clone the repository (for development)</strong></summary>

```bash
git clone https://github.com/Polloinfilzato/dm20-protocol.git
cd dm20-protocol
uv sync
```

Then add to your MCP client's config file (see [MCP Client Setup Guide](docs/MCP_CLIENTS.md) for your client's config path):

```json
{
  "mcpServers": {
    "dm20-protocol": {
      "command": "uv",
      "args": ["run", "python", "-m", "dm20_protocol"],
      "cwd": "/absolute/path/to/dm20-protocol",
      "env": {
        "DM20_STORAGE_DIR": "/path/to/your/data"
      }
    }
  }
}
```

> **Note:** Claude Desktop doesn't inherit your shell PATH. Use absolute paths for both `command` and `cwd`. Find `uv` with `which uv`.

</details>

## Quick Start

Once your MCP client is configured, try these natural language commands to get started:

```
Create a new campaign called "The Lost Kingdom"
```

```
Load the D&D 5e rules: load_rulebook source=srd
```

```
Create a level 3 High Elf Wizard named Lyra with Standard Array
```

```
Create a location called "Silverdale", a peaceful village surrounded by ancient forests
```

```
Create an NPC named Marta, an elderly herbalist who lives in Silverdale
```

```
Create a quest called "The Missing Amulet" given by Marta
```

The AI will use DM20's tools automatically — no special syntax needed. Just describe what you want in plain English. With a rulebook loaded, the Character Builder auto-populates HP, proficiencies, features, equipment, and spell slots from official rules.

For the full list of 86 tools and advanced usage, see the [User Guide](docs/GUIDE.md). For a complete example campaign, see [example/dnd/](example/dnd/example.md).

## Playing the Game

### Claude Code (recommended — full AI DM experience)

Claude Code includes slash commands that turn Claude into a complete Dungeon Master. Each command injects a detailed DM persona with situation-specific instructions and a dual-agent architecture (Narrator + Arbiter running in parallel).

```
/dm:start Curse of Strahd           ← start session (once)
/dm:action I explore the tavern      ← exploration, social, any non-combat action
/dm:action I talk to the innkeeper

/dm:combat Wolves burst from the woods!   ← starts combat
/dm:combat I attack with my longsword     ← each combat turn needs /dm:combat
/dm:combat I cast Shield as a reaction    ← still in combat
                                          ← combat ends automatically when enemies fall

/dm:action I search the wolf den     ← back to exploration after combat
/dm:save                             ← save and stop
```

**Important:** Use `/dm:combat` for **every action during combat**, not just to start it. `/dm:action` cannot advance combat turns — it's designed for exploration and social encounters only.

### Other MCP Clients (Claude Desktop, Cursor, etc.)

Without Claude Code, use DM20's tools through natural language. Add the [recommended system prompt](docs/GUIDE.md#system-prompt-recommendation) to your client's system prompt field for the best experience — it includes the full game loop, combat protocol, and DM behavior guidelines.

The AI will chain tools automatically based on your requests. The experience is good, but slash commands provide more consistent results because they inject context-specific instructions on every message.

## Optional: Smarter Rulebook Search

By default, when you search your PDF rulebooks with `ask_books`, the system uses **keyword matching** — it finds results that contain the exact words you typed. This works well for specific lookups like "fireball" or "fighter".

With RAG (Retrieval-Augmented Generation) enabled, the system understands **meaning**, not just words. For example:

| You ask | Keyword search finds | RAG search also finds |
|---|---|---|
| "tanky melee class" | Nothing (no rulebook contains "tanky") | Fighter, Paladin, Barbarian subclasses |
| "healing without magic" | Pages mentioning both "healing" and "magic" | Healer feat, Herbalism Kit, Hit Dice recovery |
| "sneaky ranged build" | Partial matches at best | Rogue/Ranger multiclass options, Skulker feat |

To enable it, run this from inside Claude Code:

```
/dm:install-rag
```

This installs ChromaDB (~200 MB), a local vector database that runs entirely on your machine — no cloud services, no API keys. The command auto-detects your setup and handles platform quirks automatically.

<details>
<summary>Manual installation (developer mode only)</summary>

```bash
uv sync --extra rag
```

</details>

> **Note:** RAG is not available on **macOS Intel (x86_64)** due to missing ML library support. Everything else works perfectly without it — keyword search covers most use cases just fine.

## Optional: Voice Narration

By default, DM20 narrates through text only. With voice enabled, the DM reads narration and NPC dialogue aloud in real time using **text-to-speech (TTS)** — no cloud subscriptions or API keys needed.

On **Apple Silicon**, DM20 uses a 3-tier engine system that picks the best engine for each context. Other platforms use Edge-TTS for all tiers:

| Tier | When used | Apple Silicon | Other platforms |
|---|---|---|---|
| **Speed** | Combat, quick actions | Kokoro — offline | Piper — offline |
| **Quality** | DM narration, NPC dialogue | Qwen3-TTS — offline | Edge-TTS — internet |
| **Fallback** | If above tiers fail | Edge-TTS — internet | Edge-TTS — internet |

> **First-use note (Apple Silicon):** The Qwen3-TTS model downloads ~1.2 GB from Hugging Face on first narrated session. The `--voice` install step itself is only ~50 MB (packages). The model is then cached and never re-downloaded.

To add voice narration to an existing install:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Polloinfilzato/dm20-protocol/main/install.sh) --voice
```

After installation, activate voice inside Claude Code with `/dm:profile` — it shows an interactive menu to set the interaction mode. Or set it directly:

```
configure_claudmaster interaction_mode="narrated"
```

> **Audio plays in your browser, not the terminal.** Run `/dm:party-mode`, open the URL shown, and leave that tab playing. For **LAN sessions** (everyone at the same table), only one device should have audio on — mute the browser tab on player devices to avoid the same narration playing from multiple speakers simultaneously.

The complete voice setup walkthrough — interaction modes, 3-tier engine details, and LAN vs remote scenarios — is in the [User Guide](docs/GUIDE.md#voice-narration).

<details>
<summary>Manual installation (developer mode only)</summary>

```bash
uv sync --extra voice
```

</details>

## Development

```bash
git clone https://github.com/Polloinfilzato/dm20-protocol.git
cd dm20-protocol
uv sync --group dev
```

Run tests:

```bash
uv run pytest tests/
```

Run the server locally:

```bash
uv run python -m dm20_protocol
```

## Solo Play — AI Dungeon Master

DM20 Protocol includes a complete **AI Dungeon Master** system for solo D&D play. Claude becomes your DM — narrating the world, roleplaying NPCs, running combat, and tracking all game state automatically.

### Game Commands

| Command | What it does |
|---------|-------------|
| `/dm:start [campaign]` | Begin or resume a game session |
| `/dm:action I search the room` | Process any player action |
| `/dm:combat goblins ambush us!` | Start or manage combat |
| `/dm:save` | Save session and pause |
| `/dm:profile [tier]` | Switch model quality: quality, balanced, economy |

### How It Works

The system uses a **dual-agent architecture** where two specialized LLM agents run in parallel on every player action:

- **Narrator** — Rich scene descriptions, NPC dialogue, atmospheric text
- **Arbiter** — Mechanical resolution, dice rolls, rule adjudication

A **DM Persona** (`.claude/dm-persona.md`) orchestrates the game loop: gather context, decide what happens, execute via tools, update state, narrate the outcome. A Python-side **Archivist** agent handles data retrieval and game state tracking without consuming LLM tokens.

**Model Profiles** let you trade quality vs token cost with a single command. All profiles use Opus with different effort levels — medium effort matches Sonnet quality with ~76% fewer output tokens:

| Profile | Model + Effort | CC Agents | Best for |
|---------|---------------|-----------|----------|
| `quality` | Opus, effort high | Opus | Boss fights, key story moments |
| `balanced` | Opus, effort medium | Opus | General play (default) |
| `economy` | Opus, effort low | Haiku | Stretching token budgets |

Switch mid-session with `/dm:profile economy` or via `configure_claudmaster(model_profile="quality")`. The profile updates both the Python-side config and the Claude Code agent files at once.

**Interaction Modes** control how the DM communicates with players. They are orthogonal to model profiles — any combination of mode × profile is valid (3 × 3 = 9 combos):

| Mode | Text | TTS Audio | STT Input | Voice Deps |
|------|------|-----------|-----------|------------|
| `classic` | Yes | No | No | None |
| `narrated` | Yes | Yes | No | `[voice]` |
| `immersive` | Yes | Yes | Yes | `[voice]` |

Set at campaign creation with `create_campaign(interaction_mode="narrated")` or switch mid-session via `configure_claudmaster(interaction_mode="immersive")`. Default is `classic` (text-only, no extra dependencies). Non-classic modes require `pip install dm20-protocol[voice]`.

> **Note:** Model profiles and effort levels are a **Claude-specific feature**. The effort parameter is only supported on Anthropic's Opus models via the Claude API. If you're using dm20-protocol with a different MCP client or LLM backend, the effort setting will have no effect — the system still works, but you won't get the quality/cost scaling that effort provides. CC agent file updates (`.claude/agents/*.md`) are specific to Claude Code.

Based on [academic research](https://arxiv.org/html/2502.19519v2) showing multi-agent GM outperforms single-agent approaches. Built on the Claudmaster architecture with session persistence, difficulty scaling, and configurable narrative style.

See the **[Player Guide](docs/PLAYER_GUIDE.md)** for how to play, or the **[Roadmap](docs/ROADMAP.md)** for what's next.

## Documentation

- [Player Guide](docs/PLAYER_GUIDE.md) — How to play solo D&D with the AI DM
- [Party Mode](docs/PARTY_MODE.md) — Multi-player web relay architecture and setup
- [User Guide](docs/GUIDE.md) — System prompt, tools reference, data structure, PDF library
- [Storage Structure](docs/STORAGE_STRUCTURE.md) — How campaign data is organized on disk
- [Development Guide](docs/DEVELOPMENT.md) — Architecture, contributing, API details
- [Roadmap](docs/ROADMAP.md) — What's implemented, what's next
- [Changelog](CHANGELOG.md) — Version history

## Credits

This project started as a fork of [gamemaster-mcp](https://github.com/study-flamingo/gamemaster-mcp) by **Joel Casimir**, who created the initial foundation for D&D campaign management via MCP.

| Component | Origin | Lines |
|-----------|--------|-------|
| Original code (v0.1.0 foundation) | Joel Casimir | ~3.9% |
| New code (library system, claudmaster, tools, tests) | DM20 Protocol contributors | ~96.1% |

The project has since been extensively rewritten and expanded with 66 MCP tools, a multi-source rulebook system, a PDF library, the Claudmaster dual-agent AI DM, and comprehensive test coverage.

This fork ([teuschb/dm20-protocol](https://github.com/teuschb/dm20-protocol)) is based on [BNDYH/dm20-protocol](https://github.com/BNDYH/dm20-protocol); see [the comparison against upstream `main`](https://github.com/BNDYH/dm20-protocol/compare/main...teuschb:dm20-protocol:main) for the fork's changes.

## License

MIT License. See [LICENSE.md](LICENSE.md).
