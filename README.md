# pi-tmux-skill

Run commands in a tmux pane, next to your conversation with the agent: you see
the live output, you can interact with it, enter password, press Ctrl+C. The
agent gets truncated progress snapshots while the command runs and a
notification when it finishes.

## What you get

Ask the agent for something that takes minutes — a download, a test run, a
benchmark, a server — and it opens a split pane in the same window (tab) with
`pi`:

- the output is live, you interact with it normally;
- Ctrl+C interrupts the command;
- the keyboard works, type a sudo password yourself;
- while the command runs, the agent receives truncated progress snapshots
  (30 s, 2:30, 10:30, 42:30) and can notice a stuck or failing process
  early; an unchanged output is not repeated;
- when the command finishes, agent receives a notification via
  `pi-intercom` with the outcome and the same truncated output; the full
  output lands in `/tmp/tmux-panes-<uid>/<pane id>.log`;
- before closing automatically, the pane shows a 15 second countdown:
  Esc keeps the pane (an interactive shell; arrow keys start with Esc and
  keep it too), Ctrl+C or Ctrl+D close the pane at once.

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
