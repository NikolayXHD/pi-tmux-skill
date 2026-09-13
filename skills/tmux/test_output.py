"""Тесты урезания вывода и его представления (output.py скила tmux)."""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import output as o


def test_when_output_fits_then_no_truncation():
    assert o.truncate('one\ntwo') == o.Truncation('one\ntwo', 2, None, False)


def test_when_output_fits_exactly_then_no_truncation():
    assert o.truncate('one\ntwo', 7) == o.Truncation('one\ntwo', 2, None, False)


def test_when_output_has_trailing_newline_then_it_is_ignored():
    assert o.truncate('one\ntwo\n') == o.Truncation('one\ntwo', 2, None, False)


def test_when_output_empty_then_no_lines():
    assert o.truncate('') == o.Truncation('', 0, None, False)


def test_when_output_overflows_then_head_and_tail_pairs_kept():
    text = '\n'.join(['a' * 20] * 12)
    assert o.truncate(text, 120) == o.Truncation(
        '\n'.join(
            ['a' * 20] * 2 + ['[… вырезано 8 строк …]'] + ['a' * 20] * 2
        ),
        12,
        ((1, 2), (11, 12)),
        False,
    )


def test_when_first_pair_overflows_then_lines_cut_in_the_middle():
    result = o.truncate('x' * 100 + '\n' + 'y' * 100, 120)
    assert result.block == (
        'x' * 17 + '[… вырезано 67 символов …]' + 'x' * 16 + '\n'
        + 'y' * 17 + '[… вырезано 66 символов …]' + 'y' * 17
    )
    assert len(result.block) == 120
    assert result.total == 2
    assert result.ranges == ((1, 1), (2, 2))
    assert result.partial


def test_when_cut_pair_skips_lines_then_marker_counts_them():
    result = o.truncate('\n'.join(['x' * 100] * 5), 120)
    block_lines = result.block.split('\n')
    assert [len(part) for part in block_lines] == [47, 23, 48]
    assert block_lines[1] == '[… вырезано 3 строки …]'
    assert result.total == 5
    assert result.ranges == ((1, 1), (5, 5))
    assert result.partial


def test_when_single_line_overflows_then_it_is_cut_in_the_middle():
    result = o.truncate('z' * 600)
    assert re.fullmatch(
        r'z{237}\[… вырезано 127 символов …\]z{236}', result.block
    )
    assert result.total == 1
    assert result.ranges == ((1, 1),)
    assert result.partial


@pytest.mark.parametrize(
    'text,limit',
    [
        ('x' * 150, 100),
        ('\n'.join(['x' * 30] * 10), 100),
        ('\n'.join(['x' * 30] * 11), 100),
        ('ы' * 700, 500),
        ('\n'.join(f'строка {i}' for i in range(200)), 500),
    ],
)
def test_when_truncated_then_block_fits_limit_and_keeps_edges(text, limit):
    lines = text.rstrip('\n').split('\n')
    result = o.truncate(text, limit)
    assert result.total == len(lines)
    assert len(result.block) <= limit
    assert result.block.startswith(lines[0][:20])
    assert result.block.endswith(lines[-1][-20:])
    if result.ranges is not None:
        assert 'вырезано' in result.block


@pytest.mark.parametrize(
    'ranges,total,partial,expected',
    [
        (
            ((1, 2), (11, 12)),
            12,
            False,
            'Показаны строки 1–2 и 11–12 из 12:'
            ' `sed -n \'1,2p;11,12p\' SRC`',
        ),
        (
            ((1, 1), (2, 2)),
            2,
            True,
            'Строки 1 и 2 из 2 показаны частично: `sed -n \'1p;2p\' SRC`',
        ),
        (
            ((1, 1),),
            1,
            True,
            'Строка 1 из 1 показана частично: `sed -n \'1p\' SRC`',
        ),
    ],
)
def test_when_truncated_then_hint_names_lines_and_sed(
    ranges, total, partial, expected
):
    truncation = o.Truncation('block', total, ranges, partial)
    assert o.hint(truncation, 'SRC') == expected


def test_when_not_truncated_then_no_hint():
    assert o.hint(o.truncate('a\nb'), 'SRC') is None


@pytest.mark.parametrize(
    'count,expected',
    [
        (1, '[… вырезана 1 строка …]'),
        (2, '[… вырезано 2 строки …]'),
        (5, '[… вырезано 5 строк …]'),
        (11, '[… вырезано 11 строк …]'),
        (12, '[… вырезано 12 строк …]'),
        (13, '[… вырезано 13 строк …]'),
        (14, '[… вырезано 14 строк …]'),
        (21, '[… вырезана 21 строка …]'),
        (22, '[… вырезано 22 строки …]'),
        (25, '[… вырезано 25 строк …]'),
        (101, '[… вырезана 101 строка …]'),
        (111, '[… вырезано 111 строк …]'),
    ],
)
def test_when_lines_marker_then_numeral_agrees(count, expected):
    assert o._lines_marker(count) == expected


@pytest.mark.parametrize(
    'count,expected',
    [
        (1, '[… вырезан 1 символ …]'),
        (2, '[… вырезано 2 символа …]'),
        (5, '[… вырезано 5 символов …]'),
        (11, '[… вырезано 11 символов …]'),
        (12, '[… вырезано 12 символов …]'),
        (21, '[… вырезан 21 символ …]'),
        (111, '[… вырезано 111 символов …]'),
    ],
)
def test_when_chars_marker_then_numeral_agrees(count, expected):
    assert o._chars_marker(count) == expected


@pytest.mark.parametrize(
    'seconds,expected',
    [
        (0, '0 с'),
        (59, '59 с'),
        (60, '1 мин 0 с'),
        (92, '1 мин 32 с'),
        (3599, '59 мин 59 с'),
        (3600, '1 ч 0 мин'),
        (5430, '1 ч 30 мин'),
    ],
)
def test_when_duration_formatted_then_units_match_scale(seconds, expected):
    assert o.format_duration(seconds) == expected


def test_when_dump_path_then_pane_log_in_dir():
    assert o.dump_path('/dumps', '%56') == '/dumps/%56.log'


def test_when_write_atomic_then_replaced_via_tmp(tmp_path, monkeypatch):
    replaced = []
    real_replace = os.replace

    def recording_replace(src, dst):
        replaced.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', recording_replace)
    path = tmp_path / 'out.log'
    o.write_atomic(str(path), 'текст')
    assert replaced == [(str(path) + '.tmp', str(path))]
    assert path.read_text(encoding='utf-8') == 'текст'
    assert not (tmp_path / 'out.log.tmp').exists()


def test_when_replace_fails_then_tmp_removed(tmp_path, monkeypatch):
    def failing_replace(src, dst):
        raise OSError('boom')

    monkeypatch.setattr(os, 'replace', failing_replace)
    with pytest.raises(OSError):
        o.write_atomic(str(tmp_path / 'out.log'), 'текст')
    assert not (tmp_path / 'out.log.tmp').exists()
