# Mira

Mira is a AI companion on terminal with a strong personality. She chats with you from the command line, remembers facts about you, and reacts based on her current mood, energy, and patience.

---

## How Mira Works

Mira connects to a remote LLM API using an OpenAI-compatible endpoint. By default she is configured for CoresHub, but you can point her at any compatible provider.

She is **not** running locally. You need an API key.

---

## Requirements

- **Python 3**
- A `.env` file in the `MIRA/` folder with these values:

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
```

You can also add an optional model name:

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

---

## What You See

At the bottom of the screen you will see a status bar like this:

```
(‿•)  Normal  |  Energy 100%  |  Patience 100%  |  0m12s  |  11:30
```

| Part | Meaning |
|------|---------|
| Face | Animated face based on her mood |
| Mood | Her current mood: normal, happy, curious, mischievous, annoyed, angry, sad |
| Energy | How awake/tired she is |
| Patience | How done with your behavior she is |
| Duration | How long the current session has been open |
| Time | Current system time |

---

## Features in v26.1

- **Chat** — Talk to Mira in a terminal UI.
- **Personality** — Casual vibes, sometimes snarky, sometimes caring. More personality comming soon.
- **Moods** — normal, happy, curious, mischievous, annoyed, angry, sad.
- **Memory** — Remembers facts you tell her across sessions.
- **Time check** — She can tell you the current time.
- **Shutdown** — Pushes her too far and she will close the session.

---

## How Moods Work

Mira’s mood comes from her **stats**.

### Patience

Patience starts at **100%**.

- Bad words and insults reduce patience.
- Normal/friendly messages slowly recover patience.
- The lower her energy, the faster she loses patience.

Thresholds:

| Patience | Effect |
|----------|--------|
| 60% | She becomes **annoyed** |
| 45% | She becomes **angry** |
| 25% | She closes the session |

### Energy

Energy starts at **100%**.

- Talking to her gives a small amount of energy.
- Replying drains a small amount.
- Being **angry** or **sad** drains energy 1.5x faster.
- When idle for 10 seconds, she recovers 1% energy.
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
├── config.json    # Personality traits, thresholds, and settings
├── memory/        # Stored facts, session state, and chat log
├── .env           # Your API key (not committed to git)
└── .gitignore     # Prevents .env and memory files from being pushed
```

---

## Known Bugs in v26.1

- **Curses screen glitches** — the terminal UI sometimes leaves escape sequences or glitched text behind, especially on shutdown or when Mira force-closes the session.
- **Arrow key crash** — pressing arrow keys while the chat is active can crash the curses input loop.
- **Scroll crash** — scrolling with a touchpad or scroll wheel can also crash the terminal UI.

These are curses/terminal input issues and have not been fixed yet.

---

## Notes

- v26.1 is **pure chat** — no tools, no web, no computer interaction.
- Mira boots up in `normal` mood with full energy and patience.
- The `.env` file and `memory/` folder are ignored by Git so your API key and chat data stay private.
- Mira is still under development.
