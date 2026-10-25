"""Diagnostics preserve transient states and never report requests as successful play."""
from dataclasses import replace
from unittest.mock import Mock
import queue
import threading

from maplebot.diagnostics import ActivityJournal,decision_message
from maplebot.settings import Settings
from maplebot.types import Status,Decision,Action,Perception


def test_history_keeps_transitions_throttles_repeats_and_is_bounded():
    now=[0.];journal=ActivityJournal(clock=lambda:now[0])
    s=Status(running=True,state='PATROL',action='walk_right',message='Running — reached left edge')
    assert journal.record(s)
    assert journal.record(s) is None
    now[0]=journal.detail_interval
    assert journal.record(replace(s,message='Running — approach mob'))
    now[0]+=journal.repeat_interval
    assert journal.record(replace(s,message='Running — approach mob'))
    # A pause is visible immediately, even in the same instant.
    assert journal.record(replace(s,paused=True,message='Game lost focus'))
    for i in range(journal.lines.maxlen+5):journal.append(f'line {i}')
    assert len(journal.lines)==journal.lines.maxlen
    assert 'Game lost focus' not in journal.text()


def test_decision_explains_missing_name_and_input_mode_without_delivery_claims():
    p=Perception(0,(800,600),(.4,.6),(400,300),True,(),.8,.5)
    d=Decision(Action.ATTACK,'ENGAGE',reason='mob in range')
    s=Settings()
    actual=decision_message(p,d,s)
    assert 'name tag missing: attacks disabled' in actual
    assert 'input requested: '+s.keys.attack in actual
    observed=decision_message(p,d,s,True)
    assert 'OBSERVE ONLY' in observed and 'would use keys:' in observed
    assert 'input requested:' not in observed


def test_gui_records_every_status_even_when_visual_refresh_is_coalesced():
    from maplebot.app.gui import App
    app=App.__new__(App)
    app._closing=False;app.events=queue.Queue();app.root=Mock()
    app.activity=ActivityJournal();app.activity_win=None
    app._show_status=Mock();app._show_preview=Mock()
    first=Status(running=True,state='PATROL',message='walking')
    paused=Status(running=True,paused=True,message='Game lost focus')
    app._on_status(first);app._on_status(paused)
    # Worker callbacks enqueue without touching Tk or changing the UI history.
    assert not app.activity.lines
    app._poll()
    assert 'walking' in app.activity.text() and 'Game lost focus' in app.activity.text()
    app._show_status.assert_called_once_with(paused)


def test_topmost_is_suspended_during_run_and_preference_is_preserved():
    from maplebot.app.gui import App
    app=App.__new__(App);app.root=Mock();app.ontop_var=Mock();app.ontop_var.get.return_value=True
    app.thread=Mock();app.thread.is_alive.return_value=True
    app._apply_ontop();app.root.attributes.assert_called_with('-topmost',False)
    app.thread=None
    app._apply_ontop();app.root.attributes.assert_called_with('-topmost',True)


def test_startup_focus_and_log_location_are_never_throttled_away():
    journal=ActivityJournal(clock=lambda:0.)
    s=Status(running=True,message='Starting…')
    journal.record(s)
    journal.record(replace(s,message='Game focus verified\nSelected game: MapleStory'))
    journal.record(replace(s,message='Run logs: logs/run'))
    assert 'Game focus verified' in journal.text() and 'Selected game: MapleStory' in journal.text()
    assert 'Run logs: logs/run' in journal.text()


def test_copy_diagnostics_includes_recent_pause_and_current_run(tmp_path):
    from types import SimpleNamespace
    from maplebot.app.gui import App
    app=App.__new__(App);app.root=Mock();app.msg_lbl=Mock();app.settings=Settings()
    app.profile=SimpleNamespace(current_map_name=lambda:'Test map',logs_dir=tmp_path)
    app.runner=SimpleNamespace(run_dir=tmp_path/'run')
    app._problems=lambda:[]
    app.activity=ActivityJournal();app.activity.append('Paused: Game lost focus; foreground verified=False')
    app.copy_diagnostics()
    copied=app.root.clipboard_append.call_args.args[0]
    assert 'Test map' in copied and 'Game lost focus' in copied
    assert 'foreground verified=False' in copied and str(tmp_path/'run') in copied
    assert 'attack='+app.settings.keys.attack in copied
