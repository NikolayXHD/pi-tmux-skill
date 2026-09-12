"""Tests for the safe tmux wrapper (tmux.py)."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import tmux as t


@pytest.fixture(autouse=True)
def inside_tmux(monkeypatch):
    """Скрипт работает только в панели tmux."""
    monkeypatch.setenv('TMUX', '/tmp/tmux-1000/default,123,0')
    monkeypatch.setenv('TMUX_PANE', '%52')


@pytest.fixture(autouse=True)
def without_pi_session(monkeypatch):
    """По умолчанию адресата уведомления нет.

    Иначе результат тестов зависит от того, запущены ли они из процесса
    агента pi, где эта переменная есть.
    """
    monkeypatch.delenv(t.PI_SESSION_ENV, raising=False)


@pytest.fixture
def pi_session(monkeypatch):
    monkeypatch.setenv(t.PI_SESSION_ENV, 'sess-1')
    return 'sess-1'


class FakeResult:
    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def record_tmux(calls, outputs=None):
    """Заменяет tmux: пишет вызовы, отвечает по подстроке среди аргументов."""
    outputs = outputs or {}

    def fake(command, *args, target):
        calls.append((command, target, args))
        for needle, reply in outputs.items():
            if needle in (command, *args):
                return reply
        return ''

    return fake


def calls_without_target(calls):
    """Вызовы tmux, ушедшие без явного таргета."""
    return [call for call in calls if not call[1]]


def script_of(command=('tail', '-f', 'x'), window='sandbox:1'):
    return t.notified_command(list(command), window)[2]


OPTS = {'cwd': None, 'pane': None}


def opts(**kw):
    return {**OPTS, **kw}


@pytest.mark.parametrize(
    'argv,expected',
    [
        (
            ['watch', '-n', '1', 'date'],
            (opts(), None, ['watch', '-n', '1', 'date']),
        ),
        (['status'], (opts(), 'status', None)),
        (['close-pane', '-p', '%56'], (opts(pane='%56'), 'close-pane', None)),
        (
            ['dump-screen', '-p', '%56'],
            (opts(pane='%56'), 'dump-screen', None),
        ),
        (['--', 'status'], (opts(), None, ['status'])),
        (['ssh', 'host', '--help'], (opts(), None, ['ssh', 'host', '--help'])),
        (
            ['-d', '/tmp', 'watch', '-n', '1'],
            (opts(cwd='/tmp'), None, ['watch', '-n', '1']),
        ),
        (['--', '-p', 'x'], (opts(), None, ['-p', 'x'])),
    ],
)
def test_when_argv_parsed_then_args_result(argv, expected):
    assert t.parse_args(argv) == expected


@pytest.mark.parametrize(
    'argv',
    [
        ['--bogus', 'x'],
        ['status', 'extra'],
        ['--'],
        ['-d'],
        ['-d', '/tmp'],
        ['-p', '%1'],
        ['close-pane', 'status'],
        ['close-pane', '-p'],
        ['status', '--'],
        ['close-pane', '-p', 'x', '--'],
        ['--close-after', '5', '--', 'x'],
    ],
)
def test_when_bad_argv_then_usage_error(argv):
    with pytest.raises(t.UsageError):
        t.parse_args(argv)


@pytest.mark.parametrize('flag', ['-t', '--target'])
def test_when_target_flag_given_then_usage_error_names_the_flag(flag):
    with pytest.raises(t.UsageError, match='-p/--pane'):
        t.parse_args([flag, '%56'])


@pytest.mark.parametrize(
    'argv',
    [
        [],
        ['-h'],
        ['--help'],
        ['status', '-h'],
    ],
)
def test_when_help_flag_then_help_requested(argv):
    with pytest.raises(t.HelpRequested):
        t.parse_args(argv)


def test_when_no_tmux_env_then_tmux_error(monkeypatch):
    monkeypatch.delenv('TMUX')
    with pytest.raises(t.TmuxError, match='TMUX is not set'):
        t.caller_pane()


def test_when_no_pane_env_then_tmux_error(monkeypatch):
    monkeypatch.delenv('TMUX_PANE')
    with pytest.raises(t.TmuxError, match='no TMUX_PANE'):
        t.caller_pane()


def test_when_pane_env_set_then_caller_pane_returned():
    assert t.caller_pane() == '%52'


def test_when_target_missing_then_type_error():
    """Таргет — обязательный параметр обёртки tmux."""
    with pytest.raises(TypeError):
        t.tmux('list-panes', '-F', '#{pane_id}')


def test_when_tmux_built_then_target_follows_command(monkeypatch):
    seen = []
    monkeypatch.setattr(
        t.subprocess,
        'run',
        lambda argv, **kwargs: (seen.append(argv), FakeResult())[1],
    )
    t.tmux('kill-pane', target='%56')
    assert seen == [['tmux', 'kill-pane', '-t', '%56']]


def test_when_tmux_fails_then_tmux_error(monkeypatch):
    monkeypatch.setattr(
        t.subprocess,
        'run',
        lambda argv, **kwargs: FakeResult(returncode=1, stderr='boom'),
    )
    with pytest.raises(t.TmuxError, match='boom'):
        t.tmux('list-panes', target='%56')


def test_when_tmux_output_then_leading_spaces_kept(monkeypatch):
    """Отступы строки — часть вывода (status, дамп экрана)."""
    monkeypatch.setattr(
        t.subprocess,
        'run',
        lambda argv, **kwargs: FakeResult(stdout='  %56 -> sandbox:1\n'),
    )
    assert t.tmux('list-panes', target='%56') == '  %56 -> sandbox:1'


def test_when_consumer_closed_pipe_then_exit_zero(monkeypatch, capsys):
    """`tmux.py dump-screen | head` — не отказ скрипта."""
    run_main(['dump-screen', '-p', '%56'], monkeypatch)

    def broken_pipe(command, *args, target):
        if command == 'capture-pane':
            raise BrokenPipeError
        return '%52 %56'

    monkeypatch.setattr(t, 'tmux', broken_pipe)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 0
    capsys.readouterr()


def test_when_window_queried_then_session_and_index_joined(monkeypatch):
    calls = []
    fake = record_tmux(
        calls, {'#{session_name}': 'sandbox', '#{window_index}': '1'}
    )
    monkeypatch.setattr(t, 'tmux', fake)
    assert t.window_of('%52') == 'sandbox:1'
    assert all(call[1] == '%52' for call in calls)
    assert calls_without_target(calls) == []


@pytest.mark.parametrize(
    'cwd,expected_args',
    [
        (None, ('-d', '-P', '-F', '#{pane_id}', '--', 'bash', '-c')),
        (
            '/tmp',
            ('-d', '-P', '-F', '#{pane_id}', '-c', '/tmp', '--', 'bash', '-c'),
        ),
    ],
)
def test_when_run_command_then_split_next_to_calling_pane(
    monkeypatch, capsys, cwd, expected_args
):
    calls = []
    fake = record_tmux(
        calls,
        {
            '#{session_name}': 'sandbox',
            '#{window_index}': '1',
            '#{pane_id}': '%58',
        },
    )
    monkeypatch.setattr(t, 'tmux', fake)
    t.run_command('%52', ['tail', '-f', 'x'], cwd=cwd)
    split = next(call for call in calls if call[0] == 'split-window')
    assert split[1] == '%52'
    script = t.notified_command(['tail', '-f', 'x'], 'sandbox:1')[2]
    assert split[2] == expected_args + (script,)
    assert '%58' in capsys.readouterr().out
    assert calls_without_target(calls) == []


@pytest.mark.parametrize(
    'fragment',
    [
        'capture-pane -p -t "$TMUX_PANE" -S -',
        'закрою через',
        f'seconds={t.CLOSE_AFTER}',
        'tmux kill-pane -t "$TMUX_PANE"',
        'exec "${SHELL:-/bin/sh}" -i',
    ],
)
def test_when_wrapped_then_dump_countdown_and_close_embedded(fragment):
    assert fragment in script_of()


def test_when_wrapped_then_first_line_guards_pane_id():
    assert script_of().splitlines()[0] == ': "${TMUX_PANE:?}"'


def test_when_wrapped_then_trap_set_before_command():
    script = script_of(command=('sleep', '5'))
    lines = script.splitlines()
    trap = next(i for i, line in enumerate(lines) if line.startswith('trap '))
    command = next(i for i, line in enumerate(lines) if "-c 'sleep 5'" in line)
    assert trap < command
    assert "trap '' INT" not in script


def test_when_wrapped_then_interrupt_leaves_shell_after_dump_and_notify(
    pi_session
):
    """Ctrl+C: дамп и уведомление уходят, потом панель остаётся с шеллом."""
    script = script_of()
    lines = script.splitlines()
    guard = next(
        i
        for i, line in enumerate(lines)
        if line.startswith('if [ "$interrupted"')
    )
    dump = lines.index(
        'tmux capture-pane -p -t "$TMUX_PANE" -S - > "$dump_path" || true'
    )
    notify = next(i for i, line in enumerate(lines) if t.NOTIFIER in line)
    assert dump < notify < guard
    assert lines[guard + 1] == '  exec "${SHELL:-/bin/sh}" -i'


def test_when_wrapped_then_dump_notify_countdown_in_order(pi_session):
    script = script_of()
    lines = script.splitlines()
    dump = next(i for i, line in enumerate(lines) if 'capture-pane' in line)
    notify = next(i for i, line in enumerate(lines) if t.NOTIFIER in line)
    countdown = next(
        i for i, line in enumerate(lines) if line.startswith('seconds=')
    )
    kill = next(i for i, line in enumerate(lines) if 'kill-pane' in line)
    assert dump < notify < countdown < kill


def test_when_no_pi_session_then_dump_and_countdown_kept():
    """Без адресата панель всё равно переживает команду и сохраняет вывод."""
    script = script_of()
    assert t.NOTIFIER not in script
    assert 'capture-pane' in script
    assert 'kill-pane' in script


def test_when_pi_session_then_notifier_gets_session_and_place(pi_session):
    script = script_of(window='sandbox:1')
    notify_line = next(
        line for line in script.splitlines() if t.NOTIFIER in line
    )
    assert f'--to {pi_session}' in notify_line
    assert '--place "$place"' in notify_line
    assert '--code "$rc"' in notify_line
    assert '--elapsed "$((SECONDS-t0))"' in notify_line
    assert '--dump "$dump_path"' in notify_line
    assert 'window=sandbox:1' in script
    assert 'place="панель $TMUX_PANE, окно $window"' in script


def test_when_window_has_space_then_quoted_in_script(pi_session):
    assert "window='my session:1'" in script_of(window='my session:1')


def test_when_command_has_newline_then_title_stays_one_line():
    script = script_of(command=('echo', 'a\nb'))
    title_line = next(
        line
        for line in script.splitlines()
        if line.startswith('tmux select-pane')
    )
    assert 'a b' in title_line
    assert title_line.endswith('>/dev/null 2>&1 || true')


@pytest.mark.parametrize(
    'command',
    [
        ['echo', 'hello'],
        ['echo', 'two words'],
        ['echo', "it's"],
        ['echo', 'привет мир'],
        ['bash', '-c', 'echo $HOME; ls "a b"'],
        ['cmake', '--build', 'build', '-j', '5'],
    ],
)
def test_when_command_has_special_args_then_quoted_losslessly(command):
    inner = shlex.join(command)
    assert f'{shlex.quote(inner)}\n' in script_of(command=command)
    assert shlex.split(inner) == command


def test_when_command_has_special_args_then_pane_shell_keeps_argv(
    monkeypatch, tmp_path
):
    """Панель исполняет команду шеллом: argv доходит без искажений."""
    argv_dump = tmp_path / 'argv.json'
    recorder = tmp_path / 'record_argv.py'
    recorder.write_text(
        'import json, sys\n'
        'open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))\n'
    )
    args = [
        'two words',
        "it's",
        'привет мир',
        'a;b',
        '$(echo x)',
        'line1\nline2',
        '-x',
        '--',
        '',
    ]
    command = [sys.executable, str(recorder), str(argv_dump), *args]
    monkeypatch.setattr(t, 'DUMP_DIR', str(tmp_path / 'dumps'))
    script = script_of(command=command)
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir()
    tmux_stub = stub_dir / 'tmux'
    tmux_stub.write_text('#!/bin/sh\nexit 0\n')
    tmux_stub.chmod(0o755)
    env = {
        **os.environ,
        'TMUX_PANE': '%99',
        'SHELL': '/bin/sh',
        'PATH': f'{stub_dir}:{os.environ["PATH"]}',
    }
    env.pop(t.PI_SESSION_ENV, None)
    subprocess.run(
        ['bash', '-c', script],
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=True,
    )
    assert json.loads(argv_dump.read_text()) == args


def test_when_wrapped_then_exit_code_preserved():
    assert script_of(command=('false',)).splitlines()[-1] == 'exit "$rc"'


def test_when_interrupted_then_dump_and_notify_run_before_shell(
    monkeypatch, tmp_path, pi_session
):
    """Ctrl+C по группе процессов: дамп и уведомление уходят, потом шелл."""
    monkeypatch.setattr(t, 'DUMP_DIR', str(tmp_path / 'dumps'))
    script = script_of(command=('sleep', '30'))
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir()
    tmux_stub = stub_dir / 'tmux'
    tmux_stub.write_text('#!/bin/sh\nexit 0\n')
    tmux_stub.chmod(0o755)
    env = {
        **os.environ,
        'TMUX_PANE': '%99',
        'SHELL': '/bin/sh',
        'PI_CODING_AGENT_DIR': str(tmp_path),
        'PATH': f'{stub_dir}:{os.environ["PATH"]}',
    }
    proc = subprocess.Popen(
        ['bash', '-c', script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=tmp_path,
        start_new_session=True,
    )
    time.sleep(1.0)
    os.killpg(proc.pid, signal.SIGINT)
    proc.wait(timeout=30)
    out = proc.stdout.read().decode()
    assert (tmp_path / 'dumps' / '%99.log').exists()
    assert 'уведомление не отправлено' in out


def test_when_command_is_user_shell_function_then_user_shell_runs_it(
    monkeypatch, tmp_path
):
    """Команда исполняется в $SHELL: функции шелла пользователя доступны."""
    user_shell = tmp_path / 'user_shell.sh'
    user_shell.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "-c" ]; then\n'
        '  eval "user_fn() { echo user-fn-ran; }; $2"\n'
        'fi\n'
    )
    user_shell.chmod(0o755)
    monkeypatch.setattr(t, 'DUMP_DIR', str(tmp_path / 'dumps'))
    script = script_of(command=('user_fn',))
    stub_dir = tmp_path / 'bin'
    stub_dir.mkdir()
    tmux_stub = stub_dir / 'tmux'
    tmux_stub.write_text('#!/bin/sh\nexit 0\n')
    tmux_stub.chmod(0o755)
    env = {
        **os.environ,
        'TMUX_PANE': '%99',
        'SHELL': str(user_shell),
        'PATH': f'{stub_dir}:{os.environ["PATH"]}',
    }
    env.pop(t.PI_SESSION_ENV, None)
    result = subprocess.run(
        ['bash', '-c', script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=True,
    )
    assert 'user-fn-ran' in result.stdout.decode()


def test_when_wrapped_then_no_pi_variables_inside():
    """Адрес и место вписаны литералами: окружение панели — окружение сервера."""
    script = script_of(command=('env',))
    assert '$PI_' not in script


def run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['tmux.py'] + argv)


@pytest.mark.parametrize(
    'argv,command,expected_args',
    [
        (['close-pane', '-p', '%58'], 'kill-pane', ()),
        (['dump-screen', '-p', '%58'], 'capture-pane', ('-p', '-S', '-')),
    ],
)
def test_when_action_pane_in_window_then_tmux_called(
    monkeypatch, capsys, argv, command, expected_args
):
    run_main(argv, monkeypatch)
    calls = []
    fake = record_tmux(calls, {'#{pane_id}': '%52 %58'})
    monkeypatch.setattr(t, 'tmux', fake)
    t.main()
    assert (command, '%58', expected_args) in calls
    assert calls_without_target(calls) == []


def test_when_dump_screen_pane_in_other_window_then_tmux_called(
    monkeypatch, capsys
):
    """dump-screen read-only: панели других окон сессии дампить можно."""
    run_main(['dump-screen', '-p', '%57'], monkeypatch)
    calls = []
    fake = record_tmux(
        calls, {'#{pane_id}': '%52 %57', 'capture-pane': 'output'}
    )
    monkeypatch.setattr(t, 'tmux', fake)
    t.main()
    assert ('capture-pane', '%57', ('-p', '-S', '-')) in calls
    assert 'output' in capsys.readouterr().out


def test_when_close_pane_in_other_window_then_error_and_no_tmux(
    monkeypatch, capsys
):
    run_main(['close-pane', '-p', '%57'], monkeypatch)
    calls = []
    fake = record_tmux(calls, {'#{pane_id}': '%52 %58'})
    monkeypatch.setattr(t, 'tmux', fake)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert 'pane %57 is NOT in the calling window' in capsys.readouterr().err
    assert [call[0] for call in calls] == ['list-panes']


def test_when_dump_pane_not_in_session_then_error_and_no_tmux(
    monkeypatch, capsys
):
    run_main(['dump-screen', '-p', '%99'], monkeypatch)
    calls = []
    fake = record_tmux(calls, {'#{pane_id}': '%52 %58'})
    monkeypatch.setattr(t, 'tmux', fake)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert 'pane %99 not found in session' in capsys.readouterr().err
    assert [call[0] for call in calls] == ['list-panes']


@pytest.mark.parametrize(
    'argv',
    [
        ['close-pane'],
        ['dump-screen'],
    ],
)
def test_when_pane_flag_missing_then_error(monkeypatch, capsys, argv):
    run_main(argv, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert '-p/--pane required' in capsys.readouterr().err


def test_when_status_then_session_map_of_calling_pane(monkeypatch, capsys):
    run_main(['status'], monkeypatch)
    calls = []
    fake = record_tmux(
        calls,
        {
            '#{session_name}': 'sandbox',
            t.WINDOW_LINE_FORMAT: '  @39 1: pi (active)',
            t.PANE_LINE_FORMAT: '  %52 -> sandbox:1.0 (pi) [pi]',
        },
    )
    monkeypatch.setattr(t, 'tmux', fake)
    t.main()
    out = capsys.readouterr().out
    assert '=== session: sandbox ===' in out
    assert '--- windows ---' in out
    assert '  @39 1: pi (active)' in out
    assert '  %52 -> sandbox:1.0 (pi) [pi]' in out
    assert calls_without_target(calls) == []


@pytest.mark.parametrize('env_var', ['TMUX', 'TMUX_PANE'])
def test_when_env_missing_then_main_exits_one(monkeypatch, capsys, env_var):
    run_main(['status'], monkeypatch)
    monkeypatch.delenv(env_var)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert 'ERROR:' in capsys.readouterr().err


def test_when_tmux_error_then_main_exits_one(monkeypatch, capsys):
    run_main(['status'], monkeypatch)

    def boom(command, *args, target):
        raise t.TmuxError('tmux broke')

    monkeypatch.setattr(t, 'tmux', boom)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert 'ERROR: tmux broke' in capsys.readouterr().err


def test_when_usage_error_then_main_exits_one(monkeypatch, capsys):
    run_main(['--bogus'], monkeypatch)
    with pytest.raises(SystemExit) as exc:
        t.main()
    assert exc.value.code == 1
    assert 'unknown flag: --bogus' in capsys.readouterr().err
