"""Тесты финального уведомления (intercom_send.py скила tmux)."""

import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import intercom_send as s


def notification(**kw):
    defaults = {
        'to': 'sess-1',
        'code': 0,
        'elapsed': None,
        'place': 'панель %56, окно sandbox:1',
        'command': None,
        'dump': None,
    }
    return Namespace(**{**defaults, **kw})


def test_when_place_given_then_message_starts_with_it():
    text = s._compose_text(notification())
    assert text.splitlines()[0] == (
        'панель %56, окно sandbox:1: команда завершилась успешно.'
    )


@pytest.mark.parametrize(
    'code,expected_outcome',
    [
        (0, 'успешно'),
        (1, 'с кодом возврата 1'),
        (130, 'с кодом возврата 130'),
    ],
)
def test_when_exit_code_given_then_outcome_named(code, expected_outcome):
    text = s._compose_text(notification(code=code))
    assert f'команда завершилась {expected_outcome}.' in text


def test_when_elapsed_given_then_duration_included():
    text = s._compose_text(notification(elapsed=92))
    assert 'завершилась успешно за 1 мин 32 с.' in text


def test_when_command_given_then_quoted_on_own_line():
    text = s._compose_text(notification(command='cmake --build build -j 5'))
    assert '`cmake --build build -j 5`' in text.splitlines()


def test_when_dump_short_then_block_and_path_included(tmp_path):
    dump = tmp_path / 'out.log'
    dump.write_text('alpha\nbeta', encoding='utf-8')
    lines = s._compose_text(notification(dump=str(dump))).splitlines()
    assert 'alpha' in lines
    assert 'beta' in lines
    assert f'Вывод сохранён: `{dump}`' in lines
    assert not any(line.startswith('Показаны строки') for line in lines)


def test_when_dump_truncated_then_hint_names_lines(tmp_path):
    dump = tmp_path / 'out.log'
    dump.write_text('\n'.join(['a' * 20] * 30), encoding='utf-8')
    lines = s._compose_text(notification(dump=str(dump))).splitlines()
    assert (
        'Показаны строки 1–11 и 20–30 из 30:'
        f" `sed -n '1,11p;20,30p' {dump}`"
    ) in lines
    assert f'Вывод сохранён: `{dump}`' in lines


def test_when_dump_missing_then_honest_line_without_block(tmp_path):
    dump = tmp_path / 'absent.log'
    text = s._compose_text(notification(command='make', dump=str(dump)))
    lines = text.splitlines()
    assert f'Вывод не удалось прочитать: `{dump}`' in lines
    assert 'Вывод сохранён' not in text
    assert lines[-1] == f'Вывод не удалось прочитать: `{dump}`'


@pytest.mark.parametrize(
    'argv,missing',
    [
        (['--to', 'sess-1', '--code', '0', '--command', 'true'], '--place'),
        (['--code', '0', '--place', 'панель %56'], '--to'),
        (['--to', 'sess-1', '--place', 'панель %56'], '--code'),
    ],
)
def test_when_required_argument_missing_then_argparse_error(
    monkeypatch, capsys, argv, missing
):
    monkeypatch.setattr(sys, 'argv', ['intercom_send.py'] + argv)
    with pytest.raises(SystemExit) as exc:
        s.main()
    assert exc.value.code == 2
    assert missing in capsys.readouterr().err
