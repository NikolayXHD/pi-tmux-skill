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
        'mkdir -p -m 700 "$dump_dir"',
        'dump_path="$dump_dir/$TMUX_PANE.log"',
        'dump_tmp="$dump_path.tmp"',
        'mv -f "$dump_tmp" "$dump_path" || rm -f "$dump_tmp"',
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


def test_when_wrapped_then_countdown_keys_close_or_keep():
    """Esc оставляет панель; Ctrl+C/Ctrl+D и таймаут закрывают, прочие игнор."""
    lines = [line.strip() for line in script_of().splitlines()]
    assert "esc=$(printf '\\033')" in lines
    assert "intr=$(printf '\\003')" in lines
    assert "del=$(printf '\\004')" in lines
    assert 'if [ "$interrupted" -ne 0 ]; then break; fi' in lines
    assert '"$esc") keep=1; break ;;' in lines
    assert '"$intr"|"$del") break ;;' in lines
    assert 'elif [ "$status" -le 128 ]; then' in lines
    close = lines.index(
        'tmux kill-pane -t "$TMUX_PANE" >/dev/null 2>&1 || true'
    )
    assert lines[close - 1] == 'if [ "$keep" -eq 0 ]; then'


def test_when_dump_dir_then_per_user_under_tmp():
    """Дампы временные: каталог под uid, чтобы общий /tmp был только наш."""
    assert t.DUMP_DIR == f'/tmp/tmux-panes-{os.getuid()}'


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
    dump = next(i for i, line in enumerate(lines) if 'capture-pane' in line)
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


def test_when_pi_session_then_progress_notifier_launched_before_command(
    pi_session,
):
    """Нотификатор стартует после trap и mkdir, до команды, и гасится до дампа."""
    lines = script_of(command=('make', '-j8')).splitlines()
    trap = next(i for i, line in enumerate(lines) if line.startswith('trap '))
    mkdir = next(
        i for i, line in enumerate(lines) if line.startswith('mkdir -p')
    )
    launch = next(
        i for i, line in enumerate(lines) if t.PROGRESS_NOTIFIER in line
    )
    command = next(
        i for i, line in enumerate(lines) if "-c 'make -j8'" in line
    )
    kill = next(
        i
        for i, line in enumerate(lines)
        if line.strip().startswith('kill "$notifier_pid"')
    )
    dump = next(i for i, line in enumerate(lines) if 'capture-pane' in line)
    assert trap < mkdir < launch < command < kill < dump


def test_when_pi_session_then_progress_notifier_gets_session_and_place(
    pi_session,
):
    line = next(
        line
        for line in script_of().splitlines()
        if t.PROGRESS_NOTIFIER in line
    )
    assert f'--to {pi_session}' in line
    assert '--place "$place"' in line
    assert '>/dev/null 2>"$dump_dir/$TMUX_PANE.notifier.log"' in line
    assert line.endswith('& notifier_pid=$!')


def test_when_pi_session_then_notifier_killed_when_running_and_reaped(
    pi_session,
):
    """Kill под защитой состояния задачи: PID переиспользуется после её выхода."""
    lines = script_of().splitlines()
    guard = lines.index('if [ "$(jobs -rp)" = "$notifier_pid" ]; then')
    assert lines[guard + 1] == '  kill "$notifier_pid" 2>/dev/null || true'
    assert lines[guard + 2] == 'fi'
    assert lines[guard + 3] == 'wait "$notifier_pid" 2>/dev/null || true'


def test_when_wrapped_then_stale_dump_removed_before_capture():
    """Дамп прошлого запуска не выдаётся за финальный вывод этого."""
    lines = script_of().splitlines()
    remove = lines.index('rm -f "$dump_path"')
    dump = next(i for i, line in enumerate(lines) if 'capture-pane' in line)
    assert remove < dump


def test_when_no_pi_session_then_dump_and_countdown_kept():
    """Без адресата панель всё равно переживает команду и сохраняет вывод."""
    script = script_of()
    assert t.NOTIFIER not in script
    assert t.PROGRESS_NOTIFIER not in script
    assert 'notifier_pid' not in script
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


def notifier_pids(pane):
    """PIDs живых нотификаторов панели (по /proc: скрипт и номер панели)."""
    pids = []
    for entry in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            cmdline = entry.read_bytes().decode(errors='replace')
        except OSError:
            continue
        if 'progress_notify.py' in cmdline and pane in cmdline:
            pids.append(entry.parent.name)
    return pids


@pytest.mark.skipif(
    not Path('/proc').is_dir(), reason='нужен /proc для поиска процессов'
)
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
    deadline = time.monotonic() + 5
    while notifier_pids('%99') and time.monotonic() < deadline:
        time.sleep(0.05)
    assert notifier_pids('%99') == []
    assert (tmp_path / 'dumps' / '%99.log').exists()
    assert 'уведомление не отправлено' in out


def countdown_stubs(tmp_path):
    """Стабы панели: tmux пишет вызовы, шелл помечает интерактивный запуск."""
    calls = tmp_path / 'tmux-calls'
    marker = tmp_path / 'shell-started'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    tmux_stub = bin_dir / 'tmux'
    tmux_stub.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 0\n')
    tmux_stub.chmod(0o755)
    shell_stub = tmp_path / 'shell.sh'
    shell_stub.write_text(
        '#!/bin/sh\n'
        f'if [ "$1" = "-i" ]; then echo interactive > "{marker}"; fi\n'
        'exit 0\n'
    )
    shell_stub.chmod(0o755)
    return calls, marker, bin_dir, shell_stub


def start_countdown(monkeypatch, tmp_path):
    """Запустить обёртку с быстрой командой и вернуть процесс и стабы."""
    monkeypatch.setattr(t, 'DUMP_DIR', str(tmp_path / 'dumps'))
    calls, marker, bin_dir, shell_stub = countdown_stubs(tmp_path)
    env = {
        **os.environ,
        'TMUX_PANE': '%99',
        'SHELL': str(shell_stub),
        'PATH': f'{bin_dir}:{os.environ["PATH"]}',
    }
    proc = subprocess.Popen(
        ['bash', '-c', script_of(command=('true',))],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=tmp_path,
        start_new_session=True,
    )
    return proc, calls, marker


@pytest.mark.parametrize(
    'keys,expect_shell,min_seconds',
    [
        (b'\x1b', True, 0),
        (b'\x1b[A', True, 0),
        (b'\x03', False, 0),
        (b'\x04', False, 0),
        (b'x', False, 1),
        (None, False, 0),
    ],
    ids=[
        'esc-keeps',
        'arrow-keeps',
        'ctrl-c-closes',
        'ctrl-d-closes',
        'other-key-ignored',
        'eof-closes',
    ],
)
def test_when_countdown_key_then_pane_kept_or_closed(
    monkeypatch, tmp_path, keys, expect_shell, min_seconds
):
    """Esc и его префикс (стрелки) оставляют шелл; Ctrl+C/Ctrl+D закрывают."""
    monkeypatch.setattr(t, 'CLOSE_AFTER', 3)
    proc, calls, marker = start_countdown(monkeypatch, tmp_path)
    started = time.monotonic()
    if keys is None:
        proc.stdin.close()
    else:
        proc.stdin.write(keys)
        proc.stdin.flush()
    try:
        proc.wait(timeout=30)
    finally:
        proc.kill()
    elapsed = time.monotonic() - started
    if keys is not None:
        proc.stdin.close()
    logged = calls.read_text() if calls.exists() else ''
    assert ('kill-pane' in logged) is not expect_shell
    assert marker.exists() is expect_shell
    assert elapsed >= min_seconds


def test_when_countdown_interrupted_then_pane_closes(monkeypatch, tmp_path):
    """Ctrl+C в отсчёте: панель закрывается сразу, шелл не остаётся."""
    monkeypatch.setattr(t, 'CLOSE_AFTER', 30)
    proc, calls, marker = start_countdown(monkeypatch, tmp_path)
    time.sleep(1.0)
    os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=30)
    finally:
        proc.stdin.close()
        proc.kill()
    assert 'kill-pane' in calls.read_text()
    assert not marker.exists()


def test_when_notifier_running_at_command_end_then_killed(
    monkeypatch, tmp_path, pi_session
):
    """С адресатом нотификатор гасится по состоянию задачи: процессов не остаётся."""
    monkeypatch.setattr(t, 'CLOSE_AFTER', 1)
    proc, calls, marker = start_countdown(monkeypatch, tmp_path)
    try:
        proc.stdin.close()
        proc.wait(timeout=30)
    finally:
        proc.kill()
    deadline = time.monotonic() + 5
    while notifier_pids('%99') and time.monotonic() < deadline:
        time.sleep(0.05)
    assert notifier_pids('%99') == []
    assert 'kill-pane' in calls.read_text()


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
