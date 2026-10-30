---
description: Initiate or manage a D&D combat encounter with initiative, tactics, and turn resolution.
argument-hint: [situation description]
allowed-tools: Task, mcp__dm20-protocol__get_game_state, mcp__dm20-protocol__get_character, mcp__dm20-protocol__get_monster_info, mcp__dm20-protocol__get_spell_info, mcp__dm20-protocol__get_npc, mcp__dm20-protocol__search_rules, mcp__dm20-protocol__roll_dice, mcp__dm20-protocol__start_combat, mcp__dm20-protocol__next_turn, mcp__dm20-protocol__end_combat, mcp__dm20-protocol__update_character, mcp__dm20-protocol__bulk_update_characters, mcp__dm20-protocol__add_item_to_character, mcp__dm20-protocol__add_event, mcp__dm20-protocol__update_game_state, mcp__dm20-protocol__configure_claudmaster
---

# DM Combat

Initiate or manage a combat encounter.

## Usage
```
/dm:combat [situation_description]
```

If a situation is described, start a new combat encounter. If combat is already active (check game state), continue managing it.

## Prerequisites

A game session must be active. If not: "No active session. Run `/dm:start` first."

## DM Persona

!`cat .claude/dm-persona.md`

## Instructions

For complex combat, you may spawn the combat-handler agent:
```
Task(subagent_type="combat-handler", description="Manage combat encounter")
```

### Starting Combat

#### 1. Identify Combatants

From `$ARGUMENTS` or the current scene, determine:
- Which PCs are present (`get_character(name_or_id=<name>)` for each)
- Which enemies are involved (`get_monster_info` for each type)
- Any allied NPCs

#### 2. Roll Initiative

For every combatant:
```
roll_dice("1d20+{dex_modifier}")
```

PC DEX modifiers come from `get_character`. Monster DEX modifiers come from `get_monster_info`.

#### 3. Start the Encounter

```
start_combat(participants=[
  {"name": "...", "initiative": N, "hp": N, "ac": N, "is_player": true/false},
  ...
])
```

Sort by initiative (highest first). Narrate the moment combat erupts.

### Combat Loop

For each turn:

#### 1. Advance Turn
```
next_turn()
```

#### 2. Player's Turn
- Announce it's their turn and summarize the battlefield
- **Wait for the player to declare their action**
- Resolve the action:
  - **Attack**: `roll_dice("1d20+{attack_mod}")` vs target AC. On hit: `roll_dice("{damage_dice}+{damage_mod}")`. Apply via `update_character`.
  - **Spell**: `get_spell_info` if needed. Resolve attack roll or save DC. Apply effects.
  - **Other**: Dash, Dodge, Disengage, Help, Hide — resolve per rules.
- Narrate the result

#### 3. Enemy Turns
Decide and execute each enemy's action. Follow the Combat Protocol from the DM persona:

- **Threat assessment**: target the biggest threat (healers, casters, low-HP targets)
- **Tactical behavior**: based on creature type (brute, ranged, spellcaster, leader, beast); every enemy has a goal and a retreat or surrender condition, none fights to the death by default
- **Situational awareness**: use terrain, focus fire, ready actions, retreat when outmatched

Scale tactics to the `difficulty` setting:
- **easy**: enemies make mistakes, poor coordination
- **normal**: solid tactics, reasonable self-preservation
- **hard**: optimal targeting, terrain use, coordinated abilities
- **deadly**: perfect tactics, no mercy, target downed PCs

Resolve each enemy attack:
```
roll_dice("1d20+{attack_mod}") → compare to PC AC
roll_dice("{damage}") → update_character(hp_change)
```

Narrate enemy actions concisely: `[Round N — Enemy Name]` then brief narrative.

#### 4. End of Round
- Check for ongoing effects (poison, concentration, etc.)
- Track death saves for downed PCs (nat 20 = up with 1 HP, nat 1 = 2 failures)
- Continue to next round

### Ending Combat

When all enemies are defeated, fled, or surrendered:

1. `end_combat()`
2. No per-encounter XP: leveling is milestone, so don't call `calculate_experience`
3. Describe aftermath: bodies, loot, environmental changes
4. `add_event(event_type="combat", title="...", description="...")` — log the encounter
5. If loot is found: `add_item_to_character` for each item
6. Transition back to exploration — describe the scene and await the next action

## Death Saves

When a PC drops to 0 HP:
1. They are unconscious and making death saves
2. On their turn: `roll_dice("1d20")`
   - 10+: success
   - 9 or less: failure
   - Natural 20: PC wakes with 1 HP
   - Natural 1: counts as 2 failures
3. Three successes: stabilized. Three failures: death.
4. On hard/deadly difficulty, enemies may attack downed PCs (auto-crit at melee range = 2 failed saves).

## Important Rules

1. **Always roll dice.** Never assume outcomes.
2. **State before story.** Update HP via tools before narrating damage.
3. **Keep pace.** Enemy turns should be fast — decide, roll, narrate briefly.
4. **Fair combat.** Rules apply consistently. The player can lose. Before applying any enemy attack a PC could react to (Shield, opportunity attacks), ask the player with the bare totals and hold the result; never treat a reaction as declined.
5. **One player turn at a time.** After resolving the player's turn, execute all enemy turns, then prompt the player again.
