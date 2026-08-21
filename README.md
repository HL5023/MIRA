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

Mira lives in the center of the terminal as a small animated kaomoji face. Her face changes with her mood, and tiny particles float around her.

At the bottom of the screen you will see a status bar like this:

```
Energy 100%     Patience 100%     0m12s     11:30
```

| Part | Meaning |
|------|---------|
| Energy | How tired she is |
| Patience | How done with your behavior she is |
| Duration | How long the current session has been open |
| Time | Current system time |

When Mira is typing you will see `MIRA IS TYPING` under her face.

---

## Features in v26.2.1

- **Terminal chat** — Talk to Mira inside a curses terminal UI.
- **Animated Mira face** — One-line kaomoji face with mood-based animations and tiny particles.
- **Personality** — Casual, sometimes snarky, sometimes caring. Short replies, slang, text emojis.
- **Moods** — normal, happy, curious, mischievous, annoyed, angry, sad.
- **Energy & Patience** — Mira gets tired and annoyed over time. Insults drain patience; idle time recovers energy.
- **Memory** — Remembers facts you tell her across sessions.
- **Tool loop** — Mira can call tools through function calling and react to the results.
- **Slash commands** — `/time`, `/tool`, `/mood`, `/kill`, `/help`.
- **File tools** — Mira can write, read, edit, delete, and list files in `~/Desktop/MiraFiles/`.
- **Time check** — She can tell you the current time.
- **Shutdown** — Pushes her too far and she will close the session.

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

`/tool` (or `/tools`) can also be used with the pipe format: `/tool write_file:path=hello.txt|content=hi`.

All other tools have been removed for now so we can focus on making the writing tools solid.

---

## How Moods Work

Mira’s mood comes from her **stats**.

### Patience

Patience starts at **100%**.

- Insults and bad words reduce patience.
- Normal/friendly messages slowly recover patience.
- The lower her energy, the faster she loses patience.

Thresholds:

| Patience | Effect |
|----------|--------|
| ≤ 60% | She becomes **annoyed** |
| ≤ 45% | She becomes **angry** |
| ≤ 25% | She may close the session if already annoyed or angry |

### Energy

Energy starts at **100%**.

- Talking to her gives a small amount of energy.
- Replying drains a small amount.
- Being **angry** or **sad** drains energy 1.5x faster.
- When idle for 10 seconds, she recovers a small amount of energy.
- At **0% energy**, she closes the session and says she is too tired.

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
├── personality.py # Mood, energy, and patience management
├── memory.py      # Long-term memory and conversation log
├── tools.py       # Computer interaction tools
├── config.json    # Personality traits, thresholds, and settings
├── memory/        # Stored facts, session state, and chat log
├── .env           # Your API key (not committed to git)
└── .gitignore     # Prevents .env and memory files from being pushed
```

---

## Known Bugs in v26.2.1

- **Screen glitches on force-close** — when Mira shuts down the session, curses can leave escape-sequence garbage behind.
- **Very small terminals** — resizing the terminal to an extremely small size can break the layout.
- **Long replies from AI** — Mira may still ignore the "keep it short" rule and ramble.
- **Inappropriate content errors** — some providers may reject prompts or outputs that contain strong language.
- **Slash commands are still rough** — `/tool` argument parsing is simple and may choke on complex quoted values.
- **File save confirmations** — Mira sometimes replies with raw tool output instead of an in-character confirmation.

---

## Notes

- v26.2.1 adds tools, but they are still experimental.
- Mira boots up in `normal` mood with full energy and patience.
- The `.env` file and `memory/` folder are ignored by Git so your API key and chat data stay private.
- Mira is still under development.
