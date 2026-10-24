# Narration script

Read by the macOS voice “Jamie (Premium)” at 175 words per minute. Drafted by Claude from
the project history, then reviewed and approved word for word by Kushal Khare before
any audio was generated. Built by `scripts/process_video.py`.

## 1. Title (0:00)

This is how I built an alarm clock as a Python command-line app, for the build exercise. The brief was deliberately open: no spec, no database, decide what to build. So the first job wasn't code. It was defining the problem.

## 2. Requirements (0:15)

Before writing anything, I had Claude ask me clarifying questions, in two short rounds, and I made the decisions that shape everything else. Alarms are saved to a file, and a foreground runner rings them. The scope is recurring days, snooze and dismiss, and editing alarms; relative timers are out. If the laptop was asleep, a late alarm still rings within a fifteen-minute grace window, and is reported as missed after that. Twice I overrode Claude's recommendation: I wanted sound on all three operating systems, not just macOS, and I chose Typer and Rich over the standard library.

## 3. Plan and milestones (0:54)

Claude then wrote an implementation plan, which I approved, and built it in ten milestones: one commit each, with the tests in the same commit. The core is a pure scheduler. It never reads the clock, so a whole morning, a closed laptop lid, or a daylight-saving change can be tested in milliseconds. And each alarm remembers the last time it handled, so nothing rings twice, even if the clock goes backwards.

## 4. Validation (1:21)

I chose how the result would be validated: fakes for anything platform-specific, CI on three operating systems, and a written validation report. There are a hundred and eighty-four tests, including property-based tests and fake-clock scenarios, plus two end-to-end tests that drive the real program through a real terminal. Claude also added a mutation check that injects fifteen deliberate bugs. When one of them first slipped through, the weak test was replaced. Now every one is caught.

## 5. What the checks caught (1:51)

The checks found real problems. Some were design pitfalls caught early; others were mistakes in Claude's first attempts. Python compares two times in the same time zone by clock reading alone, which breaks across daylight-saving changes, so every time is stored in UTC. Type-checking the code as Windows caught a failure before CI ever ran. And the first real CI run failed on two things that only happen there: forced colour output on GitHub, and Windows releasing file locks a moment late. Both are fixed and written up.

## 6. Code review (2:25)

Then Claude ran a structured code review of its own code. It found ten issues. Nine were fixed, each with a regression test confirmed to fail on the old code. For example, a grace window of zero meant alarms never rang, and two alarm commands run at the same moment could lose an alarm; that one failed three times out of three before the fix. One finding, a time-zone change while the runner is open, was consciously skipped and documented.

## 7. Demo (2:53)

Here's the result. Two alarms ring in the same minute: the first is snoozed, the second dismissed, and a second runner is refused.

## 8. Time, next steps, disclosure (3:17)

On time: from the first question to green CI took about sixty-six minutes, roughly double the thirty-minute guideline. That was just over twenty minutes defining the problem and the plan, about thirty-five building, and the rest fixing what CI found. I chose to spend it on validation rather than features. With more time, I'd fix one small thing the demo revealed: the runner still counts a finished one-time alarm as one it's watching. Everything is in the repo: the decision log, the validation report, and the scripts that build these videos. This narration is a synthetic voice reading a script Claude drafted from the project history, and that I reviewed.
