"""Тесты промежуточных уведомлений о ходе команды (progress_notify.py)."""

import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import intercom
import progress_notify as p
import tmux

PLACE = 'панель %56, окно sandbox:1'


class Recorder:
    """Заглушки цикла: снапшоты по очереди, паузы и отправки — в списках."""

    def __init__(self, snapshots, failures=0):
        self.snapshots = list(snapshots)
        self.failures = failures
        self.pauses = []
        self.sent = []

    def sleep(self, pause):
        self.pauses.append(pause)

    def capture(self, *args, **kwargs):
        return self.snapshots.pop(0)

    def deliver(self, target, text):
        if self.failures:
            self.failures -= 1
            raise intercom.IntercomError('брокер молчит')
        self.sent.append((target, text))


def run_loop(recorder, dump_path):
    p._notify_loop(
        pane='%56',
        place=PLACE,
        command='make -j8',
        target='sess-1',
        dump_path=str(dump_path),
        capture=recorder.capture,
        deliver=recorder.deliver,
        sleep=recorder.sleep,
    )


def test_when_pauses_then_each_grows_by_m():
    assert list(p._pauses()) == [30, 120, 480, 1920]


def test_when_snapshots_change_then_each_deadline_sends(tmp_path):
    recorder = Recorder(['one', 'two', 'three', 'four'])
    run_loop(recorder, tmp_path / '%56.log')
    assert recorder.pauses == [30, 120, 480, 1920]
    assert len(recorder.sent) == 4
    assert recorder.sent[0][0] == 'sess-1'
    assert 'one' in recorder.sent[0][1]
    assert 'four' in recorder.sent[-1][1]


def test_when_snapshot_repeats_then_it_is_not_sent_again(tmp_path):
    recorder = Recorder(['one', 'one', 'two', 'two'])
    run_loop(recorder, tmp_path / '%56.log')
    assert len(recorder.sent) == 2
    assert 'one' in recorder.sent[0][1]
    assert 'после 10 мин 30 с' in recorder.sent[1][1]


def test_when_delivery_fails_then_next_deadline_retries(tmp_path):
    recorder = Recorder(['one', 'one', 'one', 'one'], failures=1)
    run_loop(recorder, tmp_path / '%56.log')
    assert len(recorder.sent) == 1
    assert 'после 2 мин 30 с' in recorder.sent[0][1]


def test_when_sent_then_dump_file_holds_last_snapshot(tmp_path):
    dump = tmp_path / '%56.log'
    recorder = Recorder(['first', 'second', 'third', 'fourth'])
    run_loop(recorder, dump)
    assert dump.read_text(encoding='utf-8') == 'fourth'
    assert not (tmp_path / '%56.log.tmp').exists()


def test_when_output_short_then_message_has_block_and_file_line(tmp_path):
    dump = tmp_path / '%56.log'
    text = p._compose_text(
        PLACE, 'make -j8', 150, 'line one\nline two', str(dump)
    )
    assert text.splitlines() == [
        'панель %56, окно sandbox:1: промежуточный вывод после 2 мин 30 с.',
        '',
        '`make -j8`',
        '',
        'line one',
        'line two',
        '',
        f'Вывод на момент отправки: `{dump}`',
    ]


def test_when_output_truncated_then_message_hints_lines(tmp_path):
    dump = tmp_path / '%56.log'
    snapshot = '\n'.join(['a' * 20] * 30)
    text = p._compose_text(PLACE, 'make -j8', 30, snapshot, str(dump))
    assert (
        'Показаны строки 1–11 и 20–30 из 30:'
        f" `sed -n '1,11p;20,30p' {dump}`"
    ) in text
    assert f'Вывод на момент отправки: `{dump}`' in text


def test_when_dump_write_fails_then_message_without_tail(
    tmp_path, monkeypatch, capsys
):
    def failing_write(path, text):
        raise OSError('нет места')

    monkeypatch.setattr(p.output, 'write_atomic', failing_write)
    recorder = Recorder(['one', 'one', 'one', 'one'])
    run_loop(recorder, tmp_path / '%56.log')
    assert len(recorder.sent) == 1
    assert 'one' in recorder.sent[0][1]
    assert 'Вывод на момент отправки' not in recorder.sent[0][1]
    assert 'вывод не сохранён' in capsys.readouterr().err


def test_when_no_source_then_message_without_file_tail():
    text = p._compose_text(PLACE, 'make', 30, 'line', None)
    assert 'line' in text
    assert 'Вывод на момент отправки' not in text


def test_when_interrupted_then_quiet_exit():
    with pytest.raises(SystemExit) as exc:
        p._quiet_exit(signal.SIGINT, None)
    assert exc.value.code == 0


def test_when_interrupted_during_sleep_then_exits_immediately():
    """PEP 475: обработчик обязан выходить, иначе sleep доспит остаток."""
    script = (
        'import signal, sys, time\n'
        'sys.path.insert(0, sys.argv[1])\n'
        'import progress_notify as p\n'
        'signal.signal(signal.SIGINT, p._quiet_exit)\n'
        'print("ready", flush=True)\n'
        'try:\n'
        '    time.sleep(30)\n'
        'except SystemExit:\n'
        '    raise SystemExit(42)\n'
        'print("slept", flush=True)\n'
    )
    proc = subprocess.Popen(
        [sys.executable, '-c', script, str(Path(__file__).parent)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert proc.stdout.readline().strip() == 'ready'
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 42
    finally:
        proc.kill()


def test_when_capture_fails_then_loop_stops(tmp_path):
    def failing_capture(*args, **kwargs):
        raise tmux.TmuxError('панель исчезла')

    with pytest.raises(tmux.TmuxError):
        p._notify_loop(
            pane='%56',
            place=PLACE,
            command='make',
            target='sess-1',
            dump_path=str(tmp_path / 'x.log'),
            capture=failing_capture,
            deliver=lambda target, text: None,
            sleep=lambda pause: None,
        )


def test_when_no_pane_env_then_main_returns_error(monkeypatch, capsys):
    monkeypatch.delenv('TMUX_PANE', raising=False)
    monkeypatch.setattr(
        sys, 'argv', ['progress_notify.py', '--to', 's', '--place', PLACE]
    )
    assert p.main() == 1
    assert 'нет TMUX_PANE' in capsys.readouterr().err


def test_when_panel_unreadable_then_main_returns_error(monkeypatch, capsys):
    monkeypatch.setenv('TMUX_PANE', '%56')
    monkeypatch.setattr(
        sys, 'argv', ['progress_notify.py', '--to', 's', '--place', PLACE]
    )
    monkeypatch.setattr(signal, 'signal', lambda *args: None)

    def failing_loop(**kwargs):
        raise tmux.TmuxError('панель исчезла')

    monkeypatch.setattr(p, '_notify_loop', failing_loop)
    assert p.main() == 1
    assert 'панель не читается' in capsys.readouterr().err


@pytest.mark.parametrize(
    'argv,missing',
    [
        ([], '--to'),
        (['--to', 'sess-1'], '--place'),
    ],
)
def test_when_required_argument_missing_then_argparse_error(
    monkeypatch, capsys, argv, missing
):
    monkeypatch.setattr(sys, 'argv', ['progress_notify.py'] + argv)
    with pytest.raises(SystemExit) as exc:
        p._parse_args()
    assert exc.value.code == 2
    assert missing in capsys.readouterr().err
