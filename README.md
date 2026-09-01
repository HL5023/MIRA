# Mira

Mira is a terminal-based AI companion with a strong personality. She chats with you from the command line, remembers facts about you, reacts to your mood, and can run a small set of tools.

---

## How Mira Works

Mira connects to a remote LLM API through an OpenAI-compatible endpoint. By default she is configured for CoresHub, but you can point her at any compatible provider.

She is **not** running locally. You need an API key.

---

## Requirements

- **Python 3**
- A `.env` file in the `MIRA/` folder with these values:

```env
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://openapi.coreshub.cn/v1
```

Optional:

```env
LLM_MODEL=DeepSeek-V4-Flash
```

If you do not have a `.env` file, the program will ask for your API key when it starts.

---

## How To Run

```bash
cd MIRA
python3 main.py
```

- Press **ESC** to quit.
- Mira runs inside a curses terminal UI.
- Type your message and press **Enter**.

---

## What You See

Mira's current mood and session duration are shown in the **top header**. The chat fills the rest of the terminal and your input line is at the very bottom.

The header looks like this:

```
 MIRA // V.26.3.1-PRE                     Calm | 0m12s
```

| Part | Meaning |
|------|---------|
| Mood | How Mira currently feels |
| Duration | How long the current session has been open |

---

## Features in v26.3.1-PRE

- **Terminal chat** — Talk to Mira inside a curses terminal UI.
- **Personality** — Casual, sometimes snarky, sometimes caring. Short replies, slang, text emojis.
- **Moods** — calm, happy, sad, angry, excited, tired, bored, curious (simple emotion words).
- **Patience** — Mira's tolerance meter. Insults drain it; friendly chat and idle time recover it.
- **Memory** — Remembers facts you tell her across sessions.
- **Tool loop** — Mira can call tools through function calling and react to the results.
- **Slash commands** — `/time`, `/tool`, `/exec`, `/mood`, `/prank`, `/teach`, `/kill`, `/help`.
- **File tools** — Mira can write, read, edit, delete, and list files in `~/Desktop/MiraFiles/`.
- **Mac control tools** — mouse/keyboard control, volume, notifications, app/window management, clipboard, WiFi/AirDrop toggles.
- **Research tools** — open websites, search Bing, fetch webpage text, ask ChatGPT.
- **Pranks** — `/prank` or automatic mischief every 15-20 minutes while mischievous.
- **Teach mode** — `/teach <topic>` gives a 1-2 sentence explanation in the terminal and offers to open ChatGPT for a clearer answer.
- **Time check** — She can tell you the current time.
- **Shutdown** — Pushing her too far will close the session.

### Available Tools

| Tool | How to use it | Example |
|------|---------------|---------|
| `time` | Ask what time it is | "what time is it" |
| `write_file` | Create a file (essay, story, code, etc.) | `/tool write_file path=hello.txt content="hi there"` |
| `read_file` | Read an existing file | `/tool read_file path=hello.txt` |
| `edit_file` | Overwrite an existing file | `/tool edit_file path=hello.txt content="new content"` |
| `delete_file` | Delete a file | `/tool delete_file path=hello.txt` |
| `list_files` | List files in a directory | `/tool list_files path=.` |
| `execute_command` | Run a shell command (only when you ask for it) | `/tool execute_command command="python3 hello.py"` |

`/tool` (or `/tools`) can also be used with the pipe format: `/tool write_file:path=hi.txt|content=hi`.

`/exec <command>` is a shortcut for `/tool execute_command command=<command>`.

Tools include: `time, write_file, read_file, edit_file, delete_file, list_files, execute_command, open_file, open_website, web_search, read_website, system_info, open_app, close_app, toggle_wifi, toggle_airdrop, notify, type_text, press_key, get_volume, set_volume, move_mouse, shake_mouse, click_mouse, get_mouse_position, get_clipboard, set_clipboard, close_front_window, minimize_front_window, resize_window, ask_chatgpt, say, screenshot, search_web, search_files.`
---

## How Moods Work

Mira's mood comes from her **stats**.

### Patience

Patience starts at **100%**.

- Insults and bad words reduce patience.
- Normal/friendly messages slowly recover patience.
- Low patience makes her annoyed, then angry, then she may shut down.

Thresholds:

| Patience | Effect |
|----------|--------|
| ≤ 60% | She becomes **annoyed** |
| ≤ 45% | She becomes **angry** |
| ≤ 25% | She may close the session if already annoyed or angry |

### Sadness

Mira becomes sad in two specific situations:

1. You share something sad or bad that happened to you.
2. You say hurtful things directly to her.

No other keyword triggers mood changes.

---

## File Structure

```
MIRA/
├── main.py            # Entry point
├── brain/
│   ├── core.py        # Chat loop, intent detection, pranks, slash commands
│   ├── llm.py         # LLM API communication and prompt building
│   ├── personality.py # Mood and patience management
│   ├── memory.py      # Long-term memory and conversation log
│   ├── tools.py       # Declarative tool registry + computer interaction
│   └── terminal.py    # Curses terminal UI
├── config.json        # Personality traits, thresholds, and settings
├── memory/            # Stored facts, session state, and chat log
├── .env               # Your API key (not committed to git)
└── .gitignore         # Prevents .env and memory files from being pushed
```

---

## What Changed in v26.3.1-PRE

### Cleanup & refactor
- **Declarative tool registry** — `tools.py` tool definitions collapsed into a compact table (~230 lines saved).
- **Removed dead code** — `_extract_mira_tags`, `_extract_edit_path`, `_mood_voice`, `EMOTION_CATEGORIES`, `summarize_session`, `clear_interactions`, `short_term` storage, `events_of_type`, and the unused `mood_classifier`/`chaos` config.
- **Unified prompt builders** — `build_prompt`/`build_messages` now share one context assembler.
- **Single source of truth** — moods defined once in `personality.MOODS`; hardcoded `/Users/derek` paths replaced with `Path.home()`.

### Emotional overhaul
- **No more per-message LLM call** — intent detection is now fast, deterministic keyword + fuzzy matching (insult intensity + apology detection).
- **Patience rebalanced** — friendly chat now *recovers* patience instead of slowly draining it; insults drain it. One clean `adjust`/`annoy`/`comfort` API.
- **Mood drift wired up** — the previously-dead `mood_drift` config now makes her drift moods when idle.
- **Fact dedup** — repeated facts are no longer stored twice.

### New features
- **`say` tool** — Mira speaks out loud via macOS text-to-speech.
- **`screenshot` tool** — captures the screen to `~/Desktop/MiraFiles/`.
- **`/mood history`** — bar chart of her recent moods.
- **`/forget <fact>`** — delete a specific fact she remembers.
- **`/relationship`** — show trust/closeness/frustration stats.
- **Session summarization** — on exit she summarizes the conversation and remembers it next boot.

### Real memory
- **LLM fact extraction** — messages that look like they contain personal info are scanned by the LLM (in the background) for durable facts, on top of the fast regex path. She now remembers things like "Derek's cat is named Mochi" instead of just "my name is X".
- **Semantic recall** — ask "what did I say about my job?" or "do you remember my cat?" and she searches her facts + past messages (with synonym matching **and** character n-gram similarity) and answers with what you actually said.
- **Fact dedup** — repeated facts are no longer stored twice.

### Evolving relationship
- **Trust / closeness / frustration** now persist and change with how you treat her: insults drop trust and raise frustration, apologies recover both, and friendly chat slowly builds trust and closeness.
- The relationship level is injected into her system prompt, so she's warm and open at high trust, guarded and cold at low trust.
- **Milestones** — she knows how long she's known you and how many times she's forgiven you.

### Personality system
- **Traits are now real** — `sarcasm`, `chaos`, `affection`, `grouchiness`, `curiosity`, `stubbornness`, `helpfulness` (0-1 in config) are injected into her system prompt, so they actually change how she talks.
- **Personality editor** — `/persona show`, `/persona list`, `/persona new <name>`, `/persona apply <name>`, `/persona set <trait> <0-1>`, `/persona voice <text>`, `/persona delete <name>`, `/persona reset`. Presets with their own name/voice/traits persist in config.json.

### Context intelligence
- **User mood detection** — she reads *your* emotional state (stressed, sad, happy, angry, tired) and adjusts her tone; "im stressed" gets a gentle reassuring reply.
- **Time-aware** — she acts differently at 2am vs 9am (groggy in the morning, chatty at night).
- **Pattern recognition** — she notices when you usually talk to her ("you always message me around 19:00").
- **`/undo`** — revert her last file write (she snapshots files before writing them).
- **Config-driven settings** — prank interval, repeat threshold, and mood cooldown now live in `config.json` under `settings` instead of being hardcoded.

### Emotions
- Reduced to **8 simple core emotions**: calm, happy, sad, angry, excited, tired, bored, curious.
- Every reply starts with an emotion tag like `[happy]` or `[angry]`.
- The LLM's chosen emotion tag drives Mira's mood, so what she says matches how she feels.
- Boot coldness: after a shutdown she starts annoyed/angry instead of friendly.

### Memory
- Memory summary is now injected into the system prompt.
- Exact answers for memory questions like "how many times have I insulted you".
- Recent user messages are included as context, so she knows what was actually said.

### Tools & personality
- **Tool refusal when angry/sad** — she asks for comfort/apology before helping.
- Better `/tool` parser with quoted value support.
- PDF reading via `pypdf`.
- Teaching requests hand off to ChatGPT instead of explaining in the terminal.

### Chat behavior
- Smart insult detector with typo tolerance.
- Anti-repeat and anti-spam burst handling.
- Action-only replies (e.g. `<cries>`) logged but not shown.
- Special characters preserved in user input.
- Single-message replies enforced.

### UI
- Removed face/particle animation.
- Header shows mood label and session timer.
- Added `/status` command.

## Known Bugs in v26.3.1-PRE
- Screen glitches on force-close.
- Very small terminals can break the layout.
- Long replies may still ignore the single-message rule.
- Some short words/names can still trigger false insults.
- macOS notifications are not clickable without `terminal-notifier`.

---

## Pi Integration

Mira now has real coding-agent capabilities (like the Pi coding agent), kept in `brain/pi.py` and registered as normal tools so she can call them through the tool loop — all while keeping her personality.

- **`search_web`** — real web search that returns actual result summaries (titles, cleaned URLs, snippets) via Bing, instead of just opening a browser tab. No API key needed.
- **`search_files`** — grep-style search for text/regex across a directory, so she can find code and text in your projects.
- **`search_chat_history`** — search Mira's own past conversations.
- **`search_hermes_memory`** — search the pi agent's durable memory files (same long-term memory the coding agent keeps).

Example: ask "use search_files to find where respond_stream is defined" and she greps the codebase and tells you the file + line.

The `pi/` folder at the repo root holds the Pi agent's own data (memory, skills, sessions) and is left untouched.

## Notes

- Mira now uses 8 simple core emotions: calm, happy, sad, angry, excited, tired, bored, curious.
- Mira boots up in `calm` mood with full patience.
- The `.env` file and `memory/` folder are ignored by Git so your API key and chat data stay private.
- Mira is still under development.
