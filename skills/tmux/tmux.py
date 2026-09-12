#!/usr/bin/env python3
"""
Safe tmux wrapper -- operate in the pane the script was called from.

See SKILL.md (same directory) -- the skill this script implements.

Usage: tmux.py [<subcommand>] [flags] [--] <command...>

The pane is taken from TMUX_PANE: the script works in the pane it was called
from and never creates a window or a session. Writes (splitting off a pane,
close-pane) touch only that pane's window; reads (status, dump-screen) cover
its session.

Subcommands:
  status                  Windows and pane map of the session
  close-pane  -p <id>     Close a pane (only in the calling pane's window)
  dump-screen -p <id>     Print pane contents (any window of the session)

Flags:
  -d, --cwd <dir>   working directory
  -p, --pane <id>   pane ID (close-pane, dump-screen)

When the command finishes, the pane saves its scrollback to
~/.cache/tmux-panes/<pane id>.log, wakes the pi session with a message, shows
a 15 second countdown and closes itself; a keystroke inside the countdown
cancels the closing and leaves an interactive shell. Ctrl+C during the command
leaves the same shell: nothing is reported about it, the person who pressed it
is the one to tell.

Flags are parsed only before the command (or after a subcommand name);
after `--` or the first command word everything belongs to the command.

Examples:
  tmux.py -- ./long_task.sh       # run in a pane of the calling window
  tmux.py status                  # windows and panes
  tmux.py close-pane -p %56       # close pane
"""

import os
import shlex
import subprocess
import sys

# Сколько секунд панель ждёт после завершения команды перед закрытием.
# Нажатие клавиши отменяет закрытие, поэтому настраивать задержку не нужно.
CLOSE_AFTER = 15
DUMP_DIR = '~/.cache/tmux-panes'
ACTIONS = {'status', 'close-pane', 'dump-screen'}
FLAGS = {'-d': 'cwd', '--cwd': 'cwd', '-p': 'pane', '--pane': 'pane'}
# Идентификатор сессии pi, которой адресуется сообщение о завершении команды.
# Процесс панели порождает сервер tmux и окружения агента не наследует,
# поэтому адрес вписывается в саму команду при запуске.
PI_SESSION_ENV = 'PI_INTERCOM_SESSION_ID'
NOTIFIER = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), 'intercom_send.py'
)
# Строки status рендерит tmux: разбора имён обратно в Python нет.
WINDOW_LINE_FORMAT = (
    '  #{window_id} #{window_index}: #{window_name}'
    '#{?window_active, (active),}'
)
PANE_LINE_FORMAT = (
    '  #{pane_id} -> #{session_name}:#{window_index}.'
    '#{pane_index} (#{window_name}) [#{pane_current_command}]'
)


class UsageError(Exception):
    pass


class HelpRequested(Exception):
    pass


class TmuxError(Exception):
    pass


def tmux(command, *args, target):
    """Run a tmux command against an explicit target.

    The target is a keyword-only parameter on purpose: without -t tmux
    resolves the current client's pane, which is not necessarily the pane
    this script serves. Raise TmuxError on failure, return stdout.
    """
    result = subprocess.run(
        ['tmux', command, '-t', target, *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise TmuxError(f'tmux {command}: {result.stderr.strip()}')
    return result.stdout.rstrip('\n')


def caller_pane():
    """Pane this script was called from, as tmux names it (TMUX_PANE)."""
    if not os.environ.get('TMUX'):
        raise TmuxError('TMUX is not set; run the script inside tmux')
    pane = os.environ.get('TMUX_PANE')
    if not pane:
        raise TmuxError(
            'no TMUX_PANE in environment; run the script from inside '
            'a tmux pane'
        )
    return pane


def window_of(pane):
    """Session:index of the window holding the pane, as tmux names it."""
    session = tmux(
        'display-message', '-p', '-F', '#{session_name}', target=pane
    )
    index = tmux('display-message', '-p', '-F', '#{window_index}', target=pane)
    return f'{session}:{index}'


def run_command(pane, command, cwd=None):
    """Split off a pane next to the calling one and run the command there.

    The pane cuts the calling pane itself, so it lands in the calling window
    even when the person is looking at another one; -d keeps it inactive, so
    the keyboard stays where it was.
    """
    cmd_args = ['split-window', '-d', '-P', '-F', '#{pane_id}']
    if cwd:
        cmd_args += ['-c', cwd]
    cmd_args += ['--'] + notified_command(command, window_of(pane))
    print(tmux(cmd_args[0], *cmd_args[1:], target=pane))


def notified_command(command, window):
    """Wrap a command so that its completion wakes the pi session.

    The wrapper runs always: a tmux pane dies with its process, so without it
    the output would go away at the moment the command ends. The addressee
    comes from the agent's environment at launch time and only the
    notification depends on it: the pane environment is the tmux server's,
    where PI_SESSION_ENV is not expected to be.

    The wrapper sets the pane title, runs the command, saves the full
    scrollback into DUMP_DIR, notifies, then waits CLOSE_AFTER seconds in
    which a keystroke leaves an interactive shell instead of closing, and
    closes the pane if nobody typed. Ctrl+C during the command still gets the
    dump and the notification (exit code 130 tells the story); afterwards the
    pane is left with an interactive shell.

    Inside `bash -c` the command becomes a shell string, hence shlex.join:
    naive joining would break any argument containing a space or a quote. The
    string is delegated to the pane's `$SHELL` (tmux sets it), so user shell
    functions (`edit`, ...) are visible to the command; the wrapper itself
    stays bash because it needs traps and counters.
    """
    inner = shlex.join(command)
    dump_dir = shlex.quote(os.path.expanduser(DUMP_DIR))
    lines = [
        ': "${TMUX_PANE:?}"',
        't0=$SECONDS',
        'interrupted=0',
        "trap 'interrupted=1' INT",
        'dump_path=' + dump_dir + '/$TMUX_PANE.log',
        (
            'tmux select-pane -t "$TMUX_PANE" -T '
            + shlex.quote(_pane_title(inner))
            + ' >/dev/null 2>&1 || true'
        ),
        f'printf "%s\\n" {shlex.quote(inner)}',
        f'"${{SHELL:-/bin/sh}}" -c {shlex.quote(inner)}',
        'rc=$?',
        f'mkdir -p {dump_dir}',
        'tmux capture-pane -p -t "$TMUX_PANE" -S - > "$dump_path" || true',
    ]
    target = os.environ.get(PI_SESSION_ENV)
    if target:
        lines.append(f'window={shlex.quote(window)}')
        lines.append('place="панель $TMUX_PANE, окно $window"')
        lines.append(_notify_line(target, inner))
    lines += [
        'if [ "$interrupted" -ne 0 ]; then',
        '  exec "${SHELL:-/bin/sh}" -i',
        'fi',
        f'seconds={CLOSE_AFTER}',
        'cancelled=0',
        'while [ "$seconds" -gt 0 ]; do',
        '  printf "\\rзакрою через %3d с, нажатие клавиши отменяет  " "$seconds"',
        '  if read -r -t 1 -n 1 _; then cancelled=1; break; fi',
        '  if [ "$interrupted" -ne 0 ]; then cancelled=1; break; fi',
        '  seconds=$((seconds-1))',
        'done',
        'echo',
        'if [ "$cancelled" -eq 0 ]; then',
        '  tmux kill-pane -t "$TMUX_PANE" >/dev/null 2>&1 || true',
        'else',
        '  exec "${SHELL:-/bin/sh}" -i',
        'fi',
        'exit "$rc"',
    ]
    return ['bash', '-c', '\n'.join(lines) + '\n']


def _pane_title(command):
    """Pane title for a command: one line, so the status bar stays intact."""
    return ' '.join(command.split())


def _notify_line(target, inner):
    """Shell command that reports the finished command to the pi session.

    Literal parts go through shlex.join; "$place" and the runtime values are
    expansions on purpose.
    """
    head = shlex.join(
        [sys.executable, NOTIFIER, '--to', target, '--command', inner]
    )
    return (
        head + ' --place "$place" --code "$rc"'
        ' --elapsed "$((SECONDS-t0))" --dump "$dump_path"'
    )


def parse_args(argv):
    """Parse CLI args.

    Flags are consumed until the command starts: after `--`, or at the
    first non-flag word that is not a subcommand name. After that point
    every token belongs to the command verbatim.
    """
    if not argv or argv[0] in ('-h', '--help'):
        raise HelpRequested()
    opts = {'cwd': None, 'pane': None}
    rest = argv
    action = None
    command = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ('-h', '--help'):
            raise HelpRequested()
        if arg in ('-t', '--target'):
            raise UsageError(
                '-t/--target is not a flag of this script: a pane is given '
                'by -p/--pane, and the calling pane needs no flag'
            )
        if arg == '--':
            if action is not None:
                raise UsageError('no arguments allowed after a subcommand')
            command = rest[i + 1 :]
            break
        if arg in FLAGS:
            if i + 1 >= len(rest):
                raise UsageError(f'{arg} requires a value')
            opts[FLAGS[arg]] = rest[i + 1]
            i += 2
            continue
        if arg.startswith('-'):
            raise UsageError(f'unknown flag: {arg}')
        if arg in ACTIONS:
            if action is not None:
                raise UsageError(f'unexpected argument: {arg}')
            action = arg
            i += 1
            continue
        command = rest[i:]
        break
    if action is not None and command is not None:
        raise UsageError(f'unexpected argument: {command[0]}')
    if action is None and not command:
        raise UsageError('no command specified')
    return opts, action, command


def print_status(pane):
    """Windows and panes of the calling pane's session.

    Lines are rendered by tmux formats: nothing is parsed back, so a session
    or window name with spaces cannot break the map.
    """
    session = tmux(
        'display-message', '-p', '-F', '#{session_name}', target=pane
    )
    print(f'=== session: {session} ===')
    print('--- windows ---')
    print(tmux('list-windows', '-F', WINDOW_LINE_FORMAT, target=pane))
    print('--- panes ---')
    print(tmux('list-panes', '-s', '-F', PANE_LINE_FORMAT, target=pane))


def window_panes(caller):
    """Panes of the window holding the calling pane."""
    return tmux('list-panes', '-F', '#{pane_id}', target=caller).split()


def session_panes(caller):
    """Panes of the session holding the calling pane, any window."""
    return tmux('list-panes', '-s', '-F', '#{pane_id}', target=caller).split()


def _pane_from_flag(opts, action):
    """Pane id from -p: the flag is required for pane-scoped subcommands."""
    pane = opts['pane']
    if not pane:
        print(f'ERROR: -p/--pane required for {action}', file=sys.stderr)
        sys.exit(1)
    return pane


def _pane_in_window(caller, opts):
    """Pane id from -p, validated to belong to the calling pane's window."""
    pane = _pane_from_flag(opts, 'close-pane')
    if pane not in window_panes(caller):
        print(
            f'ERROR: pane {pane} is NOT in the calling window', file=sys.stderr
        )
        sys.exit(1)
    return pane


def _pane_in_session(caller, opts):
    """Pane id from -p, validated to exist in the session (any window).

    dump-screen is read-only and works over the whole session.
    """
    pane = _pane_from_flag(opts, 'dump-screen')
    if pane not in session_panes(caller):
        print(f'ERROR: pane {pane} not found in session', file=sys.stderr)
        sys.exit(1)
    return pane


def main():
    try:
        opts, action, command = parse_args(sys.argv[1:])
    except HelpRequested:
        print(__doc__.strip())
        return
    except UsageError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        print(
            f'  {sys.argv[0]} [<subcommand>] [flags] [--] <command...>',
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        pane = caller_pane()
        if action is None:
            run_command(pane, command, cwd=opts['cwd'])
        elif action == 'status':
            print_status(pane)
        elif action == 'close-pane':
            tmux('kill-pane', target=_pane_in_window(pane, opts))
        elif action == 'dump-screen':
            print(
                tmux(
                    'capture-pane',
                    '-p',
                    '-S',
                    '-',
                    target=_pane_in_session(pane, opts),
                )
            )
        sys.stdout.flush()
    except TmuxError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired as e:
        print(f'ERROR: tmux communication failed: {e}', file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        # Потребитель закрыл трубу (`tmux.py dump-screen | head`): это не
        # отказ скрипта. dup2 на /dev/null гасит жалобу интерпретатора при
        # завершении на незакрытый поток; у перехваченного потока дескриптора
        # может не быть — тогда глушить нечего.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        sys.exit(0)


if __name__ == '__main__':
    main()
