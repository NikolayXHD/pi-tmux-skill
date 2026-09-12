# pi-tmux-skill

Run commands in a tmux pane, next to your conversation with the agent: you see
the live output, you can interact with it, enter password, press Ctrl+C. The
agent is notified when the command finishes.

## What you get

Ask the agent for something that takes minutes — a download, a test run, a
benchmark, a server — and it opens a split pane in the same window (tab) with
`pi`:

- the output is live, you interact with it normally;
- Ctrl+C interrupts the command;
- the keyboard works, type a sudo password yourself;
- when the command finishes, agent receives result in a notification via
  `pi-intercom`;
- before closing automatically, the pane shows 15 seconds (cancellable)
  countdown.

The agent should reach for the skill for long or interactive commands. You can
also force it: `/skill:tmux <what to run>`.

## Install

```bash
pi install git:github.com/NikolayXHD/pi-tmux-skill@v1
```

To try the package without installing it:

```bash
pi -e git:github.com/NikolayXHD/pi-tmux-skill
```

## Requirements

- `tmux`, with pi running inside a tmux session;
- `python3` (standard library only, nothing to install);
- the `pi-intercom` package, installed in the same pi session. Without it the
  agent is never told that it finished, so it would have to ask or poll.

## Note

The skill runs commands on your machine, read the sources before installing it.
