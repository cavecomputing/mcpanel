from datetime import datetime

import pytest

from mcpanel import cron


def times(line, start, count=4):
    """The next count times a line matches after start."""
    parsed, found = cron.parse(line), []
    when = datetime.fromisoformat(start)
    for _ in range(count):
        when = parsed.next_after(when)
        found.append(when.strftime('%a %Y-%m-%d %H:%M'))
    return found


def test_a_daily_time():
    assert times('0 4 * * *', '2026-10-13 03:59') == ['Tue 2026-10-13 04:00', 'Wed 2026-10-14 04:00',
                                                     'Thu 2026-10-15 04:00', 'Fri 2026-10-16 04:00']
    assert times('0 4 * * *', '2026-10-13 04:00', 1) == ['Wed 2026-10-14 04:00']  # after, not at


def test_steps_lists_and_ranges():
    assert times('*/20 * * * *', '2026-10-13 10:05') == ['Tue 2026-10-13 10:20', 'Tue 2026-10-13 10:40',
                                                        'Tue 2026-10-13 11:00', 'Tue 2026-10-13 11:20']
    assert times('0 */6 * * *', '2026-10-13 00:00', 3) == ['Tue 2026-10-13 06:00', 'Tue 2026-10-13 12:00',
                                                          'Tue 2026-10-13 18:00']
    assert times('15,45 9-10 * * *', '2026-10-13 00:00') == ['Tue 2026-10-13 09:15', 'Tue 2026-10-13 09:45',
                                                            'Tue 2026-10-13 10:15', 'Tue 2026-10-13 10:45']
    assert times('0 8-18/5 * * *', '2026-10-13 00:00', 3) == ['Tue 2026-10-13 08:00', 'Tue 2026-10-13 13:00',
                                                             'Tue 2026-10-13 18:00']
    assert times('5 0 * * *', '2026-10-13 00:05', 1) == ['Wed 2026-10-14 00:05']
    assert times('07 04 * * *', '2026-10-13 00:00', 1) == ['Tue 2026-10-13 04:07']  # leading zeros


def test_names_for_months_and_days_in_any_case():
    assert times('30 5 * * Mon-FRI', '2026-10-16 06:00', 3) == ['Mon 2026-10-19 05:30', 'Tue 2026-10-20 05:30',
                                                               'Wed 2026-10-21 05:30']
    assert times('0 0 1 jan,Jul *', '2026-10-13 00:00', 2) == ['Fri 2027-01-01 00:00', 'Thu 2027-07-01 00:00']
    assert times('0 0 * * mon-5', '2026-10-17 00:00', 1) == ['Mon 2026-10-19 00:00']  # a name and a number


def test_sunday_is_0_and_7_and_ranges_may_end_on_it():
    sundays = ['Sun 2026-10-18 03:00', 'Sun 2026-10-25 03:00']
    assert times('0 3 * * 0', '2026-10-13 00:00', 2) == sundays
    assert times('0 3 * * 7', '2026-10-13 00:00', 2) == sundays
    assert times('0 3 * * sun', '2026-10-13 00:00', 2) == sundays
    weekend = ['Fri 2026-10-16 03:00', 'Sat 2026-10-17 03:00', 'Sun 2026-10-18 03:00', 'Fri 2026-10-23 03:00']
    assert times('0 3 * * 5-7', '2026-10-13 00:00') == weekend
    assert times('0 3 * * fri-sun', '2026-10-13 00:00') == weekend
    assert times('0 3 * * 5-0', '2026-10-13 00:00') == weekend


def test_day_of_month_or_day_of_week_when_both_are_restricted():
    # The 1st and 15th, and every Friday: either one will do.
    assert times('0 0 1,15 * fri', '2026-10-13 00:00') == ['Thu 2026-10-15 00:00', 'Fri 2026-10-16 00:00',
                                                          'Fri 2026-10-23 00:00', 'Fri 2026-10-30 00:00']
    # A field starting with * counts as unrestricted, so then both must match, as in cronie.
    assert times('0 0 */2 * fri', '2026-10-13 00:00', 3) == ['Fri 2026-10-23 00:00', 'Fri 2026-11-13 00:00',
                                                            'Fri 2026-11-27 00:00']
    assert times('0 0 13 * *', '2026-10-14 00:00', 1) == ['Fri 2026-11-13 00:00']


def test_the_month_always_has_to_match():
    assert times('0 0 1 2 mon', '2026-10-13 00:00', 2) == ['Mon 2027-02-01 00:00', 'Mon 2027-02-08 00:00']


def test_rare_days_are_found():
    assert times('0 0 29 2 *', '2026-10-13 00:00', 2) == ['Tue 2028-02-29 00:00', 'Sun 2032-02-29 00:00']
    assert times('0 0 31 * *', '2026-10-31 12:00', 2) == ['Thu 2026-12-31 00:00', 'Sun 2027-01-31 00:00']


def test_a_line_that_never_comes_round_has_no_next():
    assert cron.parse('0 0 30 2 *').next_after(datetime(2026, 10, 13)) is None


@pytest.mark.parametrize('macro, line', [('@yearly', '0 0 1 1 *'), ('@annually', '0 0 1 1 *'), ('@monthly', '0 0 1 * *'),
                                         ('@weekly', '0 0 * * 0'), ('@daily', '0 0 * * *'), ('@midnight', '0 0 * * *'),
                                         ('@hourly', '0 * * * *'), ('@Daily', '0 0 * * *')])
def test_macros(macro, line):
    assert times(macro, '2026-10-13 10:30', 3) == times(line, '2026-10-13 10:30', 3)


def test_wildcard_is_a_minute_or_hour_starting_with_a_star():
    assert cron.parse('*/15 4 * * *').wildcard and cron.parse('0 */2 * * *').wildcard and cron.parse('@hourly').wildcard
    assert not cron.parse('0 4 * * *').wildcard and not cron.parse('0 4 */2 * *').wildcard
    assert not cron.parse('@daily').wildcard


@pytest.mark.parametrize('line, message', [
    ('', 'five fields'),
    ('0 4 * *', 'five fields'),
    ('0 4 * * * *', 'five fields'),
    (None, 'five fields'),
    ('60 4 * * *', '"60" is not a valid minute: those go from 0 to 59'),
    ('0 24 * * *', 'hour'),
    ('0 4 0 * *', 'day of the month'),
    ('0 4 32 * *', 'day of the month'),
    ('0 4 * 13 *', 'month'),
    ('0 4 * * 8', 'day of the week'),
    ('0 4 * * monday', '"monday" is not a valid day of the week'),
    ('0 4 * jan-dex *', 'month'),
    ('0 4 * * mon/2', 'day of the week'),
    ('0 22-2 * * *', 'runs backwards'),
    ('0 4 * * fri-mon', 'runs backwards'),
    ('5/15 * * * *', 'a step goes after * or a range'),
    ('*/0 * * * *', 'whole number above 0'),
    ('*/x * * * *', 'whole number above 0'),
    ('1-5/ * * * *', 'whole number above 0'),
    ('1,,2 * * * *', 'is not a valid minute'),
    ('1, * * * *', 'is not a valid minute'),
    ('-5 * * * *', 'is not a valid minute'),
    ('1-2-3 * * * *', 'is not a valid minute'),
    ('*5 * * * *', 'is not a valid minute'),
    ('0 4 * * ½', 'is not a valid day of the week'),
    ('@reboot', 'not one of'),
    ('@every 5m', 'not one of'),
])
def test_refused_with_why(line, message):
    with pytest.raises(ValueError) as error:
        cron.parse(line)
    assert message in str(error.value)


def test_whitespace_between_fields_is_any_spaces_or_tabs():
    assert times(' 0\t4  * * * ', '2026-10-13 00:00', 1) == ['Tue 2026-10-13 04:00']


def test_a_step_bigger_than_the_range_is_its_first_value():
    assert times('*/100 * * * *', '2026-10-13 10:05', 2) == ['Tue 2026-10-13 11:00', 'Tue 2026-10-13 12:00']
