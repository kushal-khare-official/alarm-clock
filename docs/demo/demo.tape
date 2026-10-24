# alarm-clock demo, recorded with VHS (https://github.com/charmbracelet/vhs).
#
#   vhs docs/demo/demo.tape                  # from the repo root; about 3 minutes, plays sound
#   uv run python scripts/demo_audio.py      # adds the alarm sound to demo.mp4
#
# Everything shown is real: real CLI, real runner, real files (in a throwaway ALARM_CLOCK_HOME),
# real tests. Two kinds of off-camera steps, both inside Hide/Show:
#   - setup (env, tmux, pane switches), so tmux keystrokes don't clutter the video;
#   - waiting (for the alarm's minute boundary, and while the mutation check runs).
# The runner's own HH:MM:SS timestamps show where time was cut.
#
# Pane focus is verified, not assumed: in one render, keystrokes landed in the wrong tmux pane.
# After each switch the tape waits for proof ($TMUX_PANE, or the runner's reply) and times out
# rather than record a wrong video.

Output docs/demo/demo.gif
Output docs/demo/demo.mp4

Require tmux
Require uv
Require gh

Set Shell bash
Set Theme "Catppuccin Mocha"
Set FontSize 18
Set Width 1400
Set Height 900
Set Padding 24
Set Framerate 24
Set TypingSpeed 35ms
Set WaitTimeout 150s

# --- off camera: isolated env, wait for the start of a minute, start tmux ------------------
Hide
Type "tmux -L alarmdemo kill-server 2>/dev/null; export ALARM_CLOCK_HOME=$(mktemp -d) VIRTUAL_ENV_DISABLE_PROMPT=1 LANG=en_US.UTF-8"
Enter
Type "source .venv/bin/activate && export PS1='$ '"
Enter
# Start in the first seconds of a minute, so the alarms set for the next minute ring
# about a minute later: after the setup scenes, never before the runner is up.
# (The marker is typed split, so the Wait matches the output, not the command being typed.)
Type `while [ "$(date +%S)" -gt 4 ]; do sleep 0.5; done; export SOON=$(date -v+1M +%H:%M); echo "REA""DY"`
Enter
Wait+Screen /READY/
# -u: without it tmux decides this terminal can't do UTF-8 and draws ╭ ╮ ╰ ╯ ✓ as "_".
Type "tmux -u -f docs/demo/tmux.conf -L alarmdemo new-session -s demo"
Enter
Sleep 1s
Type "tmux select-pane -T 'CLI' && clear"
Enter
Sleep 500ms
Show

# --- 1. save an alarm; nothing is running yet ----------------------------------------------
Type "# Save a weekday alarm. No runner is active yet, so the CLI warns about it."
Enter
Sleep 600ms
Type "alarm add 07:30 --days weekdays -l Standup"
Enter
Sleep 3.5s

# --- 2. two alarms for the next minute -----------------------------------------------------
Type "# $SOON was set off camera to one minute from now, so the demo doesn't wait long."
Enter
Sleep 600ms
Type "alarm add $SOON -l Tea"
Enter
Sleep 1.5s
Type "alarm add $SOON -l Stretch"
Enter
Sleep 2s

# --- 3. list (the runner's pane is created now, empty, so the list stays visible above it) ---
Hide
Type "tmux split-window -v -d && tmux select-pane -t '{bottom}' -T 'alarm run'"
Enter
Sleep 1s
Type `clear; echo "IN=$TMUX_PANE"`
Enter
Wait+Screen /IN=%0/
Type "clear"
Enter
Sleep 400ms
Show
Type "alarm list"
Enter
Sleep 4s

# --- 4. start the runner in the bottom pane ------------------------------------------------
Hide
Ctrl+B
Down
Sleep 300ms
Type `echo "IN=$TMUX_PANE"`
Enter
Wait+Screen /IN=%1/
Type "clear"
Enter
Sleep 400ms
Show
Type "alarm run"
Enter
Sleep 4s

# --- 5. cut: wait for the real minute boundary ---------------------------------------------
Hide
Wait+Screen /─ ALARM ─/
Show

# --- 6. first alarm rings: snooze it -------------------------------------------------------
Sleep 4.5s
Type "s"
Sleep 1.5s

# --- 7. second alarm, same minute: dismiss it ----------------------------------------------
Wait+Screen /│ \d\d:\d\d Stretch/
Sleep 3.5s
Type "d"
Sleep 2s

# --- 8. a second runner is refused ---------------------------------------------------------
Hide
Ctrl+B
Up
Sleep 300ms
Type `echo "IN=$TMUX_PANE"`
Enter
Wait+Screen /IN=%0/
Type "clear"
Enter
Sleep 400ms
Show
Type "# A second runner is refused: an OS file lock, released even if the holder crashes."
Enter
Sleep 600ms
Type "alarm run"
Enter
Sleep 3s

# --- 9. list again: snoozed / done / on ----------------------------------------------------
Type "clear; alarm list"
Enter
Sleep 4.5s

# --- 10. stop the runner -------------------------------------------------------------------
Hide
Ctrl+B
Down
Sleep 300ms
Show
Ctrl+C
Wait+Screen /Stopped\./
Sleep 2s

# --- 11-13. the evidence, full screen ------------------------------------------------------
Hide
Ctrl+B
Up
Sleep 300ms
Type "tmux kill-pane -a && clear"
Enter
Sleep 500ms
Show
Type "# The evidence: the test suite, a mutation check, and CI on 3 operating systems."
Enter
Sleep 600ms
Type "uv run pytest -q"
Enter
Wait+Screen /passed/
Sleep 3s

Type "uv run python scripts/mutate.py   # injects 15 bugs; every one must be caught"
Enter
Sleep 2s
Hide
Wait+Screen /label edit re-arms/
Show
Sleep 4s

# JSON, not the table: "conclusion": "success" in words, rather than a small ✓ glyph.
Type "gh run list --branch main --limit 1 --json displayTitle,status,conclusion"
Enter
Wait+Screen /"conclusion":\s*"\w+"/
Sleep 5s

Hide
Type "tmux -L alarmdemo kill-server"
Enter
