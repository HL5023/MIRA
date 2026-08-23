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
- **Moods** — normal, happy, curious, mischievous, annoyed, angry, sad.
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

Tools include: `time, write_file, read_file, edit_file, delete_file, list_files, execute_command, open_file, open_website, web_search, read_website, system_info, open_app, close_app, toggle_wifi, toggle_airdrop, notify, type_text, press_key, get_volume, set_volume, move_mouse, shake_mouse, click_mouse, get_mouse_position, get_clipboard, set_clipboard, close_front_window, minimize_front_window, resize_window, ask_chatgpt.`
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
├── main.py        # Main chat loop, UI, and session logic
├── llm.py         # LLM prompt building and API communication
├── personality.py # Mood and patience management
├── memory.py      # Long-term memory and conversation log
├── tools.py       # Computer interaction tools
├── config.json    # Personality traits, thresholds, and settings
├── memory/        # Stored facts, session state, and chat log
├── .env           # Your API key (not committed to git)
└── .gitignore     # Prevents .env and memory files from being pushed
```

---

## What Changed in v26.3.1-PRE

Emotion overhaul, memory improvements, and tool personality.

### Emotions
- Reduced to **6 core emotions**: calm, happy, scared, angry, confused, sad.
- Every reply starts with an emotion tag like `[happy]` or `[angry]`.
- The LLM's chosen emotion tag drives Mira's mood, so what she says matches how she feels.
- Emotion confidence and mood memory keep her from jumping around randomly.
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

## Notes

- V26.3.1-PRE is a pre-release focused on the 6-emotion overhaul and memory improvements.
- Mira boots up in `calm` mood with full patience.
- The `.env` file and `memory/` folder are ignored by Git so your API key and chat data stay private.
- Mira is still under development.
